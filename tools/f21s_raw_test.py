"""Fredorch F21S direkt per Bluetooth ansteuern, ohne Intiface (Test).

Frage: Nimmt die Steuerung Positionen zwischen den 16 Stufen an? Intiface schickt die
Position als 0..150, nutzt aber nur jeden zehnten Wert (0, 10, ..., 150). Wenn der F21S
z.B. bei 75 wirklich zwischen 70 und 80 stehen bleibt, kann die App später 151 statt 16
Positionen fahren.

Befehle wie Intifaces Treiber (buttplug fredorch.rs, BSD 3-Clause): Modbus-RTU-Rahmen
mit CRC16 über die Bluetooth-Seriell-Brücke "YXlinksSPP".

Vorher: Intiface Central und die App trennen (nur ein Programm kann verbinden),
Hubbereich frei, Hand am Schalter. Abbruch jederzeit mit Strg+C.

    pip install bleak
    python tools/f21s_raw_test.py --demo        # feste Folge: 0, 150, 70, 80, 75, 73, 77, 0
    python tools/f21s_raw_test.py               # selbst eingeben: "75 5" = Position 75, Stufe 5
                                                # "r 6b 5" = 5 Register ab 0x6B lesen (Rückmeldung?)
"""

from __future__ import annotations

import argparse
import asyncio
import sys

NAME = "YXlinksSPP"
TX = "0000ffb1-0000-1000-8000-00805f9b34fb"     # schreiben
RX = "0000ffb2-0000-1000-8000-00805f9b34fb"     # Antworten (Benachrichtigungen)
DEMO = (0, 150, 70, 80, 75, 73, 77, 0)
DEMO_SPEED, DEMO_PAUSE_S = 5, 4.0

INIT = (   # wie Intiface beim Verbinden
    ("Programm-Modus", [0x01, 0x06, 0x00, 0x64, 0x00, 0x01]),
    ("Aufnahme", [0x01, 0x06, 0x00, 0x69, 0x00, 0x00]),
    ("Position 0, Stufe 5", [0x01, 0x10, 0x00, 0x6B, 0x00, 0x05, 0x0A, 0x00, 0x05, 0x00, 0x05,
                             0x00, 0x00, 0x00, 0x00, 0x00, 0x01]),
    ("Programm starten", [0x01, 0x06, 0x00, 0x69, 0x00, 0x01]),
    ("Wiederholen", [0x01, 0x06, 0x00, 0x6A, 0x00, 0x01]),
)


def crc16(data: bytes | list[int]) -> bytes:
    """Modbus CRC16 (Start 0xFFFF, Polynom 0xA001), niederwertiges Byte zuerst."""
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return bytes((crc & 0xFF, crc >> 8))


def frame(data: list[int]) -> bytes:
    return bytes(data) + crc16(data)


def move(pos: int, speed: int) -> bytes:
    """Fahre zu `pos` (0..150) mit Stufe `speed` (1..20), Aufbau wie Intiface."""
    if not (0 <= pos <= 150 and 1 <= speed <= 20):
        raise ValueError("Position 0..150, Stufe 1..20")
    return frame([0x01, 0x10, 0x00, 0x6B, 0x00, 0x05, 0x0A, 0x00, speed, 0x00, speed,
                  0x00, pos, 0x00, pos, 0x00, 0x01])


def read(reg: int, count: int) -> bytes:
    """Modbus "Register lesen" (Funktion 3): vielleicht steht die aktuelle Position drin."""
    return frame([0x01, 0x03, reg >> 8, reg & 0xFF, count >> 8, count & 0xFF])


async def run(args) -> None:
    from bleak import BleakClient, BleakScanner

    if args.address:
        target = args.address
    else:
        print(f"Suche {NAME} …")
        target = await BleakScanner.find_device_by_name(NAME, timeout=15)
        if target is None:
            sys.exit(f"{NAME} nicht gefunden. Gerät an? Intiface/App getrennt?")
    answers: asyncio.Queue = asyncio.Queue()
    async with BleakClient(target) as client:
        print(f"Verbunden: {client.address}")
        await client.start_notify(RX, lambda _c, d: answers.put_nowait(bytes(d)))

        async def send(label: str, data: bytes) -> None:
            while not answers.empty():
                answers.get_nowait()
            await client.write_gatt_char(TX, data, response=False)
            try:
                ans = await asyncio.wait_for(answers.get(), 0.5)
                print(f"  {label}: gesendet {data.hex(' ')}  Antwort {ans.hex(' ')}")
            except asyncio.TimeoutError:
                print(f"  {label}: gesendet {data.hex(' ')}  (keine Antwort)")

        for label, data in INIT:
            await send(label, frame(data))
        await asyncio.sleep(3)              # zur Position 0
        if args.demo:
            for pos in DEMO:
                await send(f"Position {pos}, Stufe {DEMO_SPEED}", move(pos, DEMO_SPEED))
                print(f"    → steht er bei {pos / 1.5:.0f} % des Wegs? ({DEMO_PAUSE_S:.0f} s)")
                await asyncio.sleep(DEMO_PAUSE_S)
            return
        print('Eingabe: "Position Stufe" (z.B. 75 5), "r REG ANZAHL" (hex REG, z.B. r 6b 5), q = Ende')
        loop = asyncio.get_running_loop()
        while True:
            line = (await loop.run_in_executor(None, input, "> ")).strip().lower()
            if line in ("q", "quit", "exit", ""):
                break
            try:
                parts = line.split()
                if parts[0] == "r":
                    await send("Lesen", read(int(parts[1], 16), int(parts[2]) if len(parts) > 2 else 1))
                else:
                    await send("Fahren", move(int(parts[0]), int(parts[1]) if len(parts) > 1 else DEMO_SPEED))
            except (ValueError, IndexError) as e:
                print(f"  ? {e}")
        await send("Zurück auf 0", move(0, DEMO_SPEED))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--demo", action="store_true", help="feste Testfolge fahren")
    ap.add_argument("--address", help="Bluetooth-Adresse statt Suche nach Namen")
    try:
        asyncio.run(run(ap.parse_args()))
    except KeyboardInterrupt:
        print("\nAbgebrochen. Gerät ggf. am Schalter stoppen.")


if __name__ == "__main__":
    main()
