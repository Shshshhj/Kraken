#  Module: Reddit
#  Author: code-rgb <https://github.com/code-rgb>
#          TG: [ @DeletedUser420 ]
#
#  Copyright (C) 2021 - Kraken

import re
from typing import ClassVar, Dict, List, Optional, Pattern, Union

from pyrogram.errors import MediaEmpty, WebpageCurlFailed
from pyrogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultAnimation,
    InlineQueryResultArticle,
    InlineQueryResultPhoto,
    InputTextMessageContent,
)

from .. import command, listener, module, util


class Reddit(module.Module):
    name: ClassVar[str] = "Reddit"
    uri: str = "https://meme-api.herokuapp.com/gimme"
    thumb_regex: Pattern = re.compile(
        r"https?://preview\.redd\.it/\w+\.(?:jpg|jpeg|png)\?width=(?:[2][1-9][0-9]|[3-9][0-9]{2}|[0-9]{4,})"
    )
    max_inline_results: str = "30"

    def get_rthumb(self, result: Dict) -> str:
        """get thumbnail of size 210 and above"""
        thumb = None
        if thumbs := result.get("preview"):
            while not thumb:
                if len(thumbs) == 1:
                    thumb = thumbs[0]
                else:
                    t_img = thumbs.pop(0)
                    if self.thumb_regex.search(t_img):
                        thumb = t_img
        return thumb.replace("\u0026", "&") if thumb else result.get("url")

    @staticmethod
    def parse_rpost(r: Dict) -> Optional[Dict[str, str]]:
        if not r.get("url"):
            return
        caption = f"""
<b>{r['title']}</b>
<code>Posted by u/{r['author']}
↕️ {r['ups']}</code>"""
        if r["spoiler"]:
            caption += "\n⚠️ Post marked as **SPOILER**"
        if r["nsfw"]:
            caption += "\n🔞 Post marked as **ADULT**"
        return dict(
            caption=caption,
            postlink=r["postLink"],
            subreddit=r["subreddit"],
            media_url=r["url"],
        )

    @command.desc("get post from reddit")
    async def cmd_reddit(self, ctx: command.Context):
        await ctx.respond("`Processing ...`")
        r_api = ("/".join([self.uri, ctx.input.split()[0], "5"])
                 if ctx.input else f"{self.uri}/5")
        rjson = await util.aiorequest(session=self.bot.http,
                                      url=r_api,
                                      mode="json")
        if rjson is None:
            return "ERROR : Reddit API is Down !", 5
        if rjson.get("code"):
            return f"**ERROR (code: {res['code']})** : `{res.get('message')}`", 5
        if not rjson.get("memes"):
            return "Coudn't find any reddit post with image or gif, Please try again", 5
        chat_id = ctx.msg.chat.id
        reply_id = (ctx.msg.reply_to_message.message_id
                    if ctx.msg.reply_to_message else None)
        for post in rjson["memes"]:
            try:
                if await self.send_rpost(ctx, post, chat_id, reply_id):
                    break
            except (MediaEmpty, WebpageCurlFailed):
                continue
        else:
            return "__Failed to Get Post from reddit__", 5
        await ctx.msg.delete()

    async def send_rpost(self, ctx: command.Context, rjson: Dict, chat_id: int,
                         reply_id: Optional[int]):
        if (res := self.parse_rpost(rjson)) is None:
            return False
        if ctx.client.is_bot:
            buttons = InlineKeyboardMarkup([[
                InlineKeyboardButton(f"Source: r/{res['subreddit']}",
                                     url=res["postlink"])
            ]])
            caption = res["caption"]
        else:
            caption = (
                f"{res['caption']}\nSource: [r/{res['subreddit']}]({res['postlink']})"
            )
            buttons = None
        if res["media_url"].endswith(".gif"):
            await ctx.client.send_animation(
                chat_id=chat_id,
                animation=res["media_url"],
                caption=caption,
                reply_to_message_id=reply_id,
                reply_markup=buttons,
            )
        else:
            await ctx.client.send_photo(
                chat_id=chat_id,
                photo=res["media_url"],
                caption=caption,
                reply_to_message_id=reply_id,
                reply_markup=buttons,
            )
        return True

    @listener.pattern(r"(?i)^reddit(?:\s+(?:r/)?([a-z]+)\.)?$")
    async def on_inline_query(self, query: InlineQuery) -> None:
        if subreddit := query.matches[0].group(1):
            r_api = "/".join([self.uri, subreddit, self.max_inline_results])
        else:
            r_api = "/".join([self.uri, self.max_inline_results])
        rjson = await util.aiorequest(session=self.bot.http,
                                      url=r_api,
                                      mode="json")
        if rjson is None:
            results = "Coudn't find any reddit post with image or gif, Please try again"
        elif rjson.get("code"):
            results = f"**ERROR (code: {rjson['code']})** : `{rjson.get('message')}`"
        else:
            results: List[Union[InlineQueryResultAnimation,
                                InlineQueryResultPhoto]] = []
            for post in rjson.get("memes"):
                if p_data := self.parse_rpost(post):
                    thumbnail = self.get_rthumb(p_data)
                    buttons = InlineKeyboardMarkup([[
                        InlineKeyboardButton(
                            f"Source: r/{p_data['subreddit']}",
                            url=p_data["postlink"],
                        )
                    ]])
                    if p_data["media_url"].endswith(".gif"):
                        results.append(
                            InlineQueryResultAnimation(
                                animation_url=p_data["media_url"],
                                thumb_url=thumbnail,
                                caption=p_data["caption"],
                                reply_markup=buttons,
                            ))
                    else:
                        results.append(
                            InlineQueryResultPhoto(
                                photo_url=p_data["media_url"],
                                thumb_url=thumbnail,
                                caption=p_data["caption"],
                                reply_markup=buttons,
                            ))

        if not results:
            results = "Coudn't find any reddit post with image or gif, Please try again"

        if isinstance(results, str):
            switch_pm_text = "⚠️ Error getting posts from reddit !"
            results = [
                InlineQueryResultArticle(
                    title=results,
                    input_message_content=InputTextMessageContent(results),
                    thumb_url="https://i.imgur.com/7a7aPVa.png",
                )
            ]
        else:
            switch_pm_text = f"Posts from r/{p_data['subreddit']}"

        await query.answer(
            results=results,
            cache_time=3,
            is_gallery=len(results) > 1,
            switch_pm_text=switch_pm_text,
            switch_pm_parameter="inline",
        )
