import asyncio
import base64
import pickle
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, AsyncIterator, ClassVar, Dict, Optional, Set, Tuple, Union

import aiofile
import pyrogram
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import Resource, build
from googleapiclient.http import MediaFileUpload
from motor.motor_asyncio import AsyncIOMotorDatabase
from oauthlib.oauth2.rfc6749.errors import InvalidGrantError

from .. import command, module, util


class GoogleDrive(module.Module):
    name: ClassVar[str] = "GoogleDrive"

    configs: Dict[str, str]
    creds: Credentials
    db: AsyncIOMotorDatabase
    service: Resource

    aria2: Any
    index_link: str
    parent_id: str
    task: Set[Tuple[int, asyncio.Task]]

    async def on_load(self) -> None:
        self.db = self.bot.get_db("gdrive")
        self.creds = None
        data = await self.db.find_one({"_id": self.name})

        self.configs = self.bot.getConfig.gdrive_secret
        if self.configs is None and data is None:
            self.log.warning("GoogleDrive module secret not satisfy.")
            self.bot.unload_module(self)
            return

        self.index_link = self.bot.getConfig.gdrive_index_link
        self.parent_id = self.bot.getConfig.gdrive_folder_id
        self.task = set()

        if data:
            self.creds = await util.run_sync(pickle.loads, data.get("creds"))
            # service will be overwrite if credentials is expired
            self.service = await util.run_sync(build,
                                               "drive",
                                               "v3",
                                               credentials=self.creds,
                                               cache_discovery=False)

            self.aria2 = self.bot.modules.get("Aria2")

    @command.desc("Check your GoogleDrive credentials")
    @command.alias("gdauth")
    async def cmd_gdcheck(self, ctx: command.Context) -> None:
        await ctx.respond("You are all set.")

    @command.desc("Clear/Reset your GoogleDrive credentials")
    @command.alias("gdreset")
    async def cmd_gdclear(self, ctx: command.Context) -> None:
        if not self.creds:
            return "__Credentials already empty.__"

        await self.db.delete_one({"_id": self.name})
        await asyncio.gather(self.on_load(),
                             ctx.respond("__Credentials cleared.__"))

    async def getAccessToken(self, message: pyrogram.types.Message) -> str:
        flow = InstalledAppFlow.from_client_config(
            self.configs,
            ["https://www.googleapis.com/auth/drive"],
            redirect_uri=self.configs["installed"].get("redirect_uris")[0],
        )
        auth_url, _ = flow.authorization_url(access_type="offline",
                                             prompt="consent")

        await self.bot.respond(message, "Check your **Saved Message.**")
        async with self.bot.conversation("me", timeout=60) as conv:
            request = await conv.send_message(
                f"Please visit the link:\n{auth_url}\n"
                "And reply the token here.\n**You have 60 seconds**.")

            try:
                response = await conv.get_response()
            except conv.Timeout:
                await request.delete()
                return "⚠️ <u>Timeout no token receive</u>"

        await self.bot.respond(message, "Token received...")
        token = response.text

        try:
            await asyncio.gather(
                request.delete(),
                response.delete(),
                util.run_sync(flow.fetch_token, code=token),
            )
        except InvalidGrantError:
            return ("⚠️ **Error fetching token**\n\n"
                    "__Refresh token is invalid, expired, revoked, "
                    "or does not match the redirection URI.__")

        self.creds = flow.credentials
        credential = await util.run_sync(pickle.dumps, self.creds)

        await self.db.find_one_and_update({"_id": self.name},
                                          {"$set": {
                                              "creds": credential
                                          }},
                                          upsert=True)
        await self.on_load()

        return "Credentials created."

    async def authorize(self,
                        message: pyrogram.types.Message) -> Optional[bool]:
        if not self.creds or not self.creds.valid:
            if self.creds and self.creds.expired and self.creds.refresh_token:
                self.log.info("Refreshing credentials")
                await util.run_sync(self.creds.refresh, Request())

                credential = await util.run_sync(pickle.dumps, self.creds)
                await self.db.find_one_and_update(
                    {"_id": self.name}, {"$set": {
                        "creds": credential
                    }})
            else:
                await asyncio.gather(
                    self.bot.respond(message,
                                     "Credential is empty, generating..."),
                    asyncio.sleep(2.5),
                )

                ret = await self.getAccessToken(message)

                await self.bot.respond(message, ret)
                if self.creds is None:
                    return False

            await self.on_load()

    async def createFolder(self,
                           folderName: str,
                           folderId: Optional[str] = None) -> str:
        folder_metadata = {
            "name": folderName,
            "mimeType": "application/vnd.google-apps.folder",
        }
        if folderId is not None:
            folder_metadata["parents"] = [folderId]
        elif folderId is None and self.parent_id is not None:
            folder_metadata["parents"] = [self.parent_id]

        folder = await util.run_sync(self.service.files().create(
            body=folder_metadata, fields="id", supportsAllDrives=True).execute)
        return folder["id"]

    async def uploadFolder(
        self,
        sourceFolder: Path,
        *,
        gid: Optional[str] = None,
        parent_id: Optional[str] = None,
        msg: Optional[pyrogram.types.Message] = None,
    ) -> AsyncIterator[asyncio.Task]:
        for content in sourceFolder.iterdir():
            if content.is_dir():
                childFolder = await self.createFolder(content.name, parent_id)
                async for task in self.uploadFolder(content,
                                                    gid=gid,
                                                    parent_id=childFolder,
                                                    msg=msg):
                    yield task
            elif content.is_file():
                file = util.File(content)
                files = await self.uploadFile(file, parent_id)
                if isinstance(files, str):  # Skip because file size is 0
                    continue

                file.content, file.start_time = files, util.time.sec()
                file.invoker = msg

                yield self.bot.loop.create_task(file.progress(update=False),
                                                name=gid)

    async def uploadFile(
        self,
        file: Union[util.File, util.aria2.Download],
        parent_id: Optional[str] = None,
    ) -> MediaFileUpload:
        body = {"name": file.name, "mimeType": file.mime_type}
        if parent_id is not None:
            body["parents"] = [parent_id]
        elif parent_id is None and self.parent_id is not None:
            body["parents"] = [self.parent_id]

        if file.path.stat().st_size > 0:
            media_body = MediaFileUpload(
                file.path,
                mimetype=file.mime_type,
                resumable=True,
                chunksize=50 * 1024 * 1024,
            )
            files = await util.run_sync(
                self.service.files().create,
                body=body,
                media_body=media_body,
                fields="id, size, webContentLink",
                supportsAllDrives=True,
            )
        else:
            media_body = MediaFileUpload(file.path, mimetype=file.mime_type)
            files = await util.run_sync(self.service.files().create(
                body=body,
                media_body=media_body,
                fields="id, size, webContentLink",
                supportsAllDrives=True,
            ).execute)

            return files.get("id")

        if not isinstance(file, util.File):
            files.gid, files.name = file.gid, file.name
            files.start_time = util.time.sec()

        return files

    async def downloadFile(self, ctx: command.Context,
                           msg: pyrogram.types.Message) -> Optional[Path]:
        downloadPath = ctx.bot.getConfig.downloadPath

        before = util.time.sec()
        last_update_time = None
        human = util.misc.human_readable_bytes
        time = util.time.format_duration_td
        if msg.document:
            file_name = msg.document.file_name
        elif msg.audio:
            file_name = msg.audio.file_name
        elif msg.video:
            file_name = msg.video.file_name
        elif msg.sticker:
            file_name = msg.sticker.file_name
        elif msg.photo:
            date = datetime.fromtimestamp(msg.photo.date)
            file_name = f"photo_{date.strftime('%Y-%m-%d_%H-%M-%S')}.jpg"
        elif msg.voice:
            date = datetime.fromtimestamp(msg.voice.date)
            file_name = f"audio_{date.strftime('%Y-%m-%d_%H-%M-%S')}.ogg"

        def prog_func(current: int, total: int) -> None:
            nonlocal last_update_time

            percent = current / total
            after = util.time.sec() - before
            now = datetime.now()

            try:
                speed = round(current / after, 2)
                eta = timedelta(seconds=int(round((total - current) / speed)))
            except ZeroDivisionError:
                speed = 0
                eta = timedelta(seconds=0)
            bullets = "●" * int(round(percent * 10)) + "○"
            if len(bullets) > 10:
                bullets = bullets.replace("○", "")

            space = "    " * (10 - len(bullets))
            progress = (
                f"`{file_name}`\n"
                f"Status: **Downloading**\n"
                f"Progress: [{bullets + space}] {round(percent * 100)}%\n"
                f"__{human(current)} of {human(total)} @ "
                f"{human(speed, postfix='/s')}\neta - {time(eta)}__\n\n")
            # Only edit message once every 5 seconds to avoid ratelimits
            if (last_update_time is None
                    or (now - last_update_time).total_seconds() >= 5):
                self.bot.loop.create_task(ctx.respond(progress))

                last_update_time = now

        file_path = downloadPath / file_name
        file_path = await ctx.bot.client.download_media(msg,
                                                        file_name=file_path,
                                                        progress=prog_func)

        if file_path is not None:
            return Path(file_path)

        return

    @command.desc("Mirror Magnet/Torrent/Link/Message Media into GoogleDrive")
    @command.usage("[Magnet/Torrent/Link or reply to message]")
    async def cmd_gdmirror(self, ctx: command.Context) -> Optional[str]:
        if not ctx.input and not ctx.msg.reply_to_message:
            return "__Either link nor media found.__"
        if ctx.input and ctx.msg.reply_to_message:
            return "__Can't pass link while replying to message.__"

        if ctx.msg.reply_to_message:
            reply_msg = ctx.msg.reply_to_message

            if reply_msg.media:
                task = self.bot.loop.create_task(
                    self.downloadFile(ctx, reply_msg))
                self.task.add((ctx.msg.message_id, task))
                try:
                    await task
                except asyncio.CancelledError:
                    return "__Transmission aborted.__"
                else:
                    path = task.result()
                    self.task.remove((ctx.msg.message_id, task))

                if path.suffix == ".torrent":
                    async with aiofile.async_open(path, "rb") as afp:
                        types = base64.b64encode(await afp.read())
                else:
                    file = util.File(path)
                    files = await self.uploadFile(file)
                    file.content, file.invoker = files, ctx.msg
                    file.start_time = util.time.sec()
                    if self.index_link is not None:
                        file.index_link = self.index_link

                    task = self.bot.loop.create_task(file.progress())
                    self.task.add((ctx.msg.message_id, task))
                    try:
                        await task
                    except asyncio.CancelledError:
                        return "__Transmission aborted.__"
                    else:
                        self.task.remove((ctx.msg.message_id, task))

                    return
            elif reply_msg.text:
                types = reply_msg.text
            else:
                return "__Unsupported types of download.__"
        else:
            types = ctx.input

        if not self.bot.modules.get("Aria2"):
            return "__Mirroring torrent file/url needs Aria2 loaded.__"

        ret = await self.aria2.addDownload(types, ctx.msg)
        if ret is not None:
            return ret
