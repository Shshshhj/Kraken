import re
from typing import TYPE_CHECKING, Any, MutableMapping, Tuple

import pyrogram
from pyrogram.filters import Filter, create

from .. import command, module, util
from .base import Base
from .raw import Message

if TYPE_CHECKING:
    from .bot import Bot


class CommandDispatcher(Base):
    commands: MutableMapping[str, command.Command]

    def __init__(self: "Bot", **kwargs: Any) -> None:
        self.commands = {}

        super().__init__(**kwargs)

    def register_command(self: "Bot", mod: module.Module, name: str,
                         func: command.CommandFunc) -> None:
        cmd = command.Command(name, mod, func)

        if name in self.commands:
            orig = self.commands[name]
            raise module.ExistingCommandError(orig, cmd)

        self.commands[name] = cmd

        for alias in cmd.aliases:
            if alias in self.commands:
                orig = self.commands[alias]
                raise module.ExistingCommandError(orig, cmd, alias=True)

            self.commands[alias] = cmd

    def unregister_command(self: "Bot", cmd: command.Command) -> None:
        del self.commands[cmd.name]

        for alias in cmd.aliases:
            try:
                del self.commands[alias]
            except KeyError:
                continue

    def register_commands(self: "Bot", mod: module.Module) -> None:
        for name, func in util.misc.find_prefixed_funcs(mod, "cmd_"):
            done = False

            try:
                self.register_command(mod, name, func)
                done = True
            finally:
                if not done:
                    self.unregister_commands(mod)

    def unregister_commands(self: "Bot", mod: module.Module) -> None:
        to_unreg = []

        for name, cmd in self.commands.items():
            if name != cmd.name:
                continue

            if cmd.module == mod:
                to_unreg.append(cmd)

        for cmd in to_unreg:
            self.unregister_command(cmd)

    def command_predicate(self: "Bot") -> Filter:

        async def func(_, __, msg: pyrogram.types.Message):
            if msg.text is not None and msg.text.startswith(self.prefix):
                parts = msg.text.split()
                parts[0] = parts[0][len(self.prefix):]
                msg.segments = parts
                return True

            return False

        return create(func)

    def sudo_command_predicate(self: "Bot") -> Filter:

        async def func(_, __, msg: pyrogram.types.Message):
            if (msg.text is not None and msg.text.startswith(self.sudoprefix)
                    and msg.from_user.id == self.uid):
                parts = msg.text.split()
                parts[0] = parts[0][len(self.sudoprefix):]
                msg.segments = parts
                return True

            return False

        return create(func)

    @staticmethod
    def outgoing_flt() -> Filter:
        return create(
            lambda _, __, msg: msg.via_bot is None and not msg.scheduled and
            not (msg.forward_from or msg.forward_sender_name) and not (
                msg.from_user and msg.from_user.is_bot) and
            (msg.outgoing or (msg.from_user and msg.from_user.is_self)) and
            not (msg.chat and msg.chat.type == "channel" and msg.edit_date))

    async def on_command(self: "Bot", client: pyrogram.Client,
                         msg: pyrogram.types.Message) -> None:
        cmd = None
        msg = Message._parse(msg)

        try:
            try:
                cmd = self.commands[msg.segments[0]]
            except KeyError:
                return

            if (cmd.module.name == "GoogleDrive"
                    and not cmd.module.disabled) and cmd.name not in [
                        "gdreset", "gdclear"
                    ]:
                ret = await cmd.module.authorize(msg)

                if ret is False:
                    return

            cmd_len = len(self.prefix) + len(msg.segments[0]) + 1
            matches = None
            if cmd.pattern is not None:
                if isinstance(cmd.pattern, str):
                    cmd.pattern = re.compile(cmd.pattern)

                if msg.reply_to_message:
                    matches = list(
                        cmd.pattern.finditer(msg.reply_to_message.text))
                elif msg.text:
                    matches = list(cmd.pattern.finditer(msg.text[cmd_len:]))

            ctx = command.Context(self, client, msg, msg.segments, cmd_len,
                                  matches)

            try:
                ret = await cmd.func(ctx)

                if ret is not None:
                    if isinstance(ret, Tuple):
                        await ctx.respond(ret[0], delete_after=int(ret[1]))
                    else:
                        await ctx.respond(ret)
            except pyrogram.errors.MessageNotModified:
                cmd.module.log.warning(
                    f"Command '{cmd.name}' triggered a message edit with no changes"
                )
            except Exception as e:  # skipcq: PYL-W0703
                cmd.module.log.error(f"Error in command '{cmd.name}'",
                                     exc_info=e)
                if (input_text :=
                    (ctx.input if ctx.input is not None else msg.text) or ""):
                    input_text = f"**Input:**\n{input_text}\n\n"
                await ctx.respond(
                    f"{input_text}**ERROR**:\n⚠️ Failed to execute command:\n"
                    f"```{util.error.format_exception(e)}```")

            await self.dispatch_event("command", cmd, msg)
        except Exception as e:  # skipcq: PYL-W0703
            if cmd is not None:
                cmd.module.log.error("Error in command handler", exc_info=e)

            await self.respond(
                msg,
                "⚠️ Error in command handler:\n"
                f"```{util.error.format_exception(e)}```",
            )
