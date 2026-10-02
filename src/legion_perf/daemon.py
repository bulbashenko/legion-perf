"""Системный демон: D-Bus API к железу с проверкой прав через polkit.

Состояние, выставленное через демон, восстанавливается после выхода из сна.
После загрузки демон ничего не меняет: остаются значения прошивки.
"""

# Без `from __future__ import annotations`: dbus-fast читает сигнатуры D-Bus
# из аннотаций-строк ("b", "as"), а отложенные аннотации их ломают.

import argparse
import asyncio
import contextvars
import logging
import signal
from pathlib import Path
from typing import ClassVar, Protocol

from dbus_fast import BusType, DBusError, Message, MessageType, PropertyAccess, RequestNameReply, Variant
from dbus_fast.aio import MessageBus
from dbus_fast.service import ServiceInterface, dbus_property, method

from . import __version__
from .api import (
    BUS_NAME,
    ERROR_HARDWARE,
    ERROR_NOT_AUTHORIZED,
    ERROR_NOT_SUPPORTED,
    INTERFACE,
    OBJECT_PATH,
    POLKIT_ACTION,
    Feature,
)
from .hardware import Hardware, HardwareError

log = logging.getLogger("legion_perf.daemon")

# Прошивке нужно время после пробуждения, иначе она может перетереть наши значения.
RESUME_DELAY = 2.0

LOGIN1 = "org.freedesktop.login1"
LOGIN1_MANAGER = "org.freedesktop.login1.Manager"
SLEEP_MATCH = f"type='signal',sender='{LOGIN1}',interface='{LOGIN1_MANAGER}',member='PrepareForSleep'"

# dbus-fast не передаёт в методы отправителя. Обработчик сообщений кладёт его сюда
# до вызова метода, а asyncio копирует контекст в задачу метода. Если отправитель
# не дошёл (None), авторизация отказывает.
_caller: contextvars.ContextVar[str | None] = contextvars.ContextVar("caller", default=None)


def _remember_caller(msg: Message) -> None:
    if msg.message_type is MessageType.METHOD_CALL:
        _caller.set(msg.sender)


class Authorizer(Protocol):
    async def check(self, sender: str | None) -> None: ...


class PolkitAuthorizer:
    def __init__(self, bus: MessageBus) -> None:
        self._bus = bus

    async def check(self, sender: str | None) -> None:
        if not sender:
            raise DBusError(ERROR_NOT_AUTHORIZED, "unknown caller")
        reply = await self._bus.call(
            Message(
                destination="org.freedesktop.PolicyKit1",
                path="/org/freedesktop/PolicyKit1/Authority",
                interface="org.freedesktop.PolicyKit1.Authority",
                member="CheckAuthorization",
                signature="(sa{sv})sa{ss}us",
                body=[
                    ["system-bus-name", {"name": Variant("s", sender)}],
                    POLKIT_ACTION,
                    {},
                    1,  # AllowUserInteraction: неактивной сессии polkit покажет запрос пароля
                    "",
                ],
            )
        )
        if reply is None or reply.message_type is MessageType.ERROR:
            log.error("polkit check failed: %s", reply.body if reply else "no reply")
            raise DBusError(ERROR_NOT_AUTHORIZED, "polkit check failed")
        if not reply.body[0][0]:
            raise DBusError(ERROR_NOT_AUTHORIZED, "not authorized")


class AllowAll:
    """Только для --session (разработка и тесты): polkit на сессионной шине нет."""

    async def check(self, sender: str | None) -> None:
        if not sender:
            raise DBusError(ERROR_NOT_AUTHORIZED, "unknown caller")


class LegionInterface(ServiceInterface):
    _PROPERTY: ClassVar[dict[Feature, str]] = {
        Feature.EXTREME_COOLING: "ExtremeCooling",
    }

    def __init__(self, hw: Hardware, authorizer: Authorizer) -> None:
        super().__init__(INTERFACE)
        self._hw = hw
        self._auth = authorizer
        # Значения, выставленные через демон с момента загрузки: их и возвращаем после сна.
        self.desired: dict[Feature, bool] = {}
        self._lock = asyncio.Lock()

    # ── Чтение ──────────────────────────────────────────────────────────────

    def _read(self, feature: Feature, default: bool) -> bool:
        control = self._hw.controls.get(feature)
        if control is None:
            return default
        try:
            return control.get()
        except HardwareError as e:
            log.error("%s: %s", feature.value, e)
            return default

    @dbus_property(access=PropertyAccess.READ)
    def Version(self) -> "s":
        return __version__

    @dbus_property(access=PropertyAccess.READ)
    def Features(self) -> "as":
        return [f.value for f in self._hw.controls]

    @dbus_property(access=PropertyAccess.READ)
    def ExtremeCooling(self) -> "b":
        return self._read(Feature.EXTREME_COOLING, False)

    # ── Запись ──────────────────────────────────────────────────────────────

    async def _set(self, feature: Feature, value: bool) -> None:
        control = self._hw.controls.get(feature)
        if control is None:
            raise DBusError(ERROR_NOT_SUPPORTED, f"{feature.value} is not available")
        await self._auth.check(_caller.get())
        async with self._lock:
            try:
                control.set(value)
            except HardwareError as e:
                log.error("%s: %s", feature.value, e)
                raise DBusError(ERROR_HARDWARE, str(e)) from e
            self.desired[feature] = value
        log.info("%s set to %s by %s", feature.value, value, _caller.get())
        self.emit_properties_changed({self._PROPERTY[feature]: value})

    @method()
    async def SetExtremeCooling(self, on: "b") -> None:
        await self._set(Feature.EXTREME_COOLING, on)

    # ── Сон ─────────────────────────────────────────────────────────────────

    async def restore(self) -> None:
        """Возвращает значения, выставленные до сна, если прошивка их сбросила."""
        async with self._lock:
            changed = {}
            for feature, value in self.desired.items():
                control = self._hw.controls[feature]
                try:
                    if control.get() != value:
                        control.set(value)
                        changed[self._PROPERTY[feature]] = value
                        log.info("restored %s = %s after resume", feature.value, value)
                except HardwareError as e:
                    log.error("restore %s: %s", feature.value, e)
        if changed:
            self.emit_properties_changed(changed)


async def watch_sleep(bus: MessageBus, iface: LegionInterface) -> None:
    tasks: set[asyncio.Task[None]] = set()

    async def resume() -> None:
        await asyncio.sleep(RESUME_DELAY)
        await iface.restore()

    def on_signal(msg: Message) -> None:
        if (
            msg.message_type is MessageType.SIGNAL
            and msg.interface == LOGIN1_MANAGER
            and msg.member == "PrepareForSleep"
            and msg.body == [False]
        ):
            task = asyncio.ensure_future(resume())
            tasks.add(task)
            task.add_done_callback(tasks.discard)

    bus.add_message_handler(on_signal)
    await bus.call(
        Message(
            destination="org.freedesktop.DBus",
            path="/org/freedesktop/DBus",
            interface="org.freedesktop.DBus",
            member="AddMatch",
            signature="s",
            body=[SLEEP_MATCH],
        )
    )


async def serve(bus: MessageBus, hw: Hardware, authorizer: Authorizer) -> LegionInterface:
    iface = LegionInterface(hw, authorizer)
    bus.add_message_handler(_remember_caller)
    bus.export(OBJECT_PATH, iface)
    await watch_sleep(bus, iface)
    reply = await bus.request_name(BUS_NAME)
    if reply is not RequestNameReply.PRIMARY_OWNER:
        raise SystemExit(f"cannot own {BUS_NAME}: {reply.name}")
    log.info("legion-perf daemon %s ready", __version__)
    return iface


async def run(bus_type: BusType, root: Path) -> None:
    bus = await MessageBus(bus_type=bus_type).connect()
    authorizer: Authorizer = PolkitAuthorizer(bus) if bus_type is BusType.SYSTEM else AllowAll()
    await serve(bus, Hardware.detect(root), authorizer)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, bus.disconnect)
    await bus.wait_for_disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(description="Legion Performance Controller daemon")
    parser.add_argument(
        "--session", action="store_true", help="use the session bus without polkit (development only)"
    )
    parser.add_argument(
        "--root", type=Path, default=Path("/"), help="filesystem root for hardware detection (testing)"
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    asyncio.run(run(BusType.SESSION if args.session else BusType.SYSTEM, args.root))


if __name__ == "__main__":
    main()
