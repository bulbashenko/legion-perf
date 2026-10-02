"""Демон на отдельной шине D-Bus с фейковым железом и подменённым polkit."""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from dbus_fast import DBusError, Message, MessageType
from dbus_fast.aio import MessageBus

from legion_perf import daemon
from legion_perf.api import (
    BUS_NAME,
    ERROR_NOT_AUTHORIZED,
    ERROR_NOT_SUPPORTED,
    INTERFACE,
    OBJECT_PATH,
)
from legion_perf.daemon import LegionInterface, serve
from legion_perf.hardware import Hardware

from .conftest import EC_IO, ec_byte


class FakeAuthorizer:
    def __init__(self, allow: bool = True) -> None:
        self.allow = allow
        self.senders: list[str | None] = []

    async def check(self, sender: str | None) -> None:
        self.senders.append(sender)
        if not self.allow or not sender:
            raise DBusError(ERROR_NOT_AUTHORIZED, "denied")


class Env:
    def __init__(
        self,
        address: str,
        service: MessageBus,
        client: MessageBus,
        iface: LegionInterface,
        auth: FakeAuthorizer,
        root: Path,
    ) -> None:
        self.address = address
        self.service, self.client, self.iface, self.auth, self.root = service, client, iface, auth, root

    async def call(self, member: str, signature: str = "", body: list | None = None) -> Message:
        reply = await self.client.call(
            Message(
                destination=BUS_NAME,
                path=OBJECT_PATH,
                interface=INTERFACE,
                member=member,
                signature=signature,
                body=body or [],
            )
        )
        assert reply is not None
        return reply

    async def props(self) -> dict:
        reply = await self.client.call(
            Message(
                destination=BUS_NAME,
                path=OBJECT_PATH,
                interface="org.freedesktop.DBus.Properties",
                member="GetAll",
                signature="s",
                body=[INTERFACE],
            )
        )
        assert reply is not None and reply.message_type is MessageType.METHOD_RETURN
        return {k: v.value for k, v in reply.body[0].items()}


@pytest.fixture
async def env(dbus_address: str, fake_root: Path) -> AsyncIterator[Env]:
    service = await MessageBus(bus_address=dbus_address).connect()
    client = await MessageBus(bus_address=dbus_address).connect()
    auth = FakeAuthorizer()
    iface = await serve(service, Hardware.detect(fake_root), auth)
    yield Env(dbus_address, service, client, iface, auth, fake_root)
    client.disconnect()
    service.disconnect()


async def test_get_all(env: Env) -> None:
    props = await env.props()
    assert props["Features"] == ["extreme-cooling"]
    assert props["ExtremeCooling"] is False


async def test_set_extreme_cooling(env: Env) -> None:
    reply = await env.call("SetExtremeCooling", "b", [True])
    assert reply.message_type is MessageType.METHOD_RETURN
    assert ec_byte(env.root, 0xBD) == 0x40
    assert (await env.props())["ExtremeCooling"] is True


async def test_authorizer_sees_real_caller(env: Env) -> None:
    await env.call("SetExtremeCooling", "b", [True])
    assert env.auth.senders == [env.client.unique_name]


async def test_denied_call_does_not_touch_hardware(env: Env) -> None:
    env.auth.allow = False
    reply = await env.call("SetExtremeCooling", "b", [True])
    assert reply.message_type is MessageType.ERROR
    assert reply.error_name == ERROR_NOT_AUTHORIZED
    assert ec_byte(env.root, 0xBD) == 0x00
    assert env.iface.desired == {}


async def test_unsupported_feature(dbus_address: str, fake_root: Path) -> None:
    (fake_root / EC_IO).unlink()
    service = await MessageBus(bus_address=dbus_address).connect()
    client = await MessageBus(bus_address=dbus_address).connect()
    await serve(service, Hardware.detect(fake_root), FakeAuthorizer())
    reply = await client.call(
        Message(
            destination=BUS_NAME,
            path=OBJECT_PATH,
            interface=INTERFACE,
            member="SetExtremeCooling",
            signature="b",
            body=[True],
        )
    )
    assert reply is not None and reply.error_name == ERROR_NOT_SUPPORTED
    client.disconnect()
    service.disconnect()


async def test_properties_changed_signal(env: Env) -> None:
    got: asyncio.Future[list] = asyncio.get_running_loop().create_future()

    def handler(msg: Message) -> None:
        if msg.member == "PropertiesChanged" and not got.done():
            got.set_result(msg.body)

    env.client.add_message_handler(handler)
    await env.client.call(
        Message(
            destination="org.freedesktop.DBus",
            path="/org/freedesktop/DBus",
            interface="org.freedesktop.DBus",
            member="AddMatch",
            signature="s",
            body=[f"type='signal',sender='{BUS_NAME}',member='PropertiesChanged'"],
        )
    )
    await env.call("SetExtremeCooling", "b", [True])
    iface, changed, _ = await asyncio.wait_for(got, 2)
    assert iface == INTERFACE
    assert changed["ExtremeCooling"].value is True


async def test_restore_after_firmware_reset(env: Env) -> None:
    await env.call("SetExtremeCooling", "b", [True])
    (env.root / EC_IO).write_bytes(bytes(256))  # прошивка сбросила EC во сне

    await env.iface.restore()

    assert ec_byte(env.root, 0xBD) == 0x40


async def test_restore_leaves_untouched_values(env: Env) -> None:
    """После загрузки демон ничего не выставлял — восстанавливать нечего."""
    (env.root / EC_IO).write_bytes(bytes([0x40]) * 256)
    await env.iface.restore()
    assert ec_byte(env.root, 0xBD) == 0x40
    assert env.iface.desired == {}


async def test_resume_signal_triggers_restore(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(daemon, "RESUME_DELAY", 0)
    await env.call("SetExtremeCooling", "b", [True])
    (env.root / EC_IO).write_bytes(bytes(256))

    login1 = await MessageBus(bus_address=env.address).connect()
    await login1.request_name("org.freedesktop.login1")
    await login1.send(
        Message.new_signal(
            "/org/freedesktop/login1",
            "org.freedesktop.login1.Manager",
            "PrepareForSleep",
            "b",
            [False],
        )
    )
    for _ in range(50):
        if ec_byte(env.root, 0xBD) == 0x40:
            break
        await asyncio.sleep(0.02)
    assert ec_byte(env.root, 0xBD) == 0x40
    login1.disconnect()
