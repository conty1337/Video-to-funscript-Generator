"""JMC JSS57P2N (Motortreiber im Fredorch F21S): RS232/Modbus RTU nur LESEND untersuchen.

NUR LESEN: Das Skript kennt ausschließlich die Modbus-Funktionen 03 (Read Holding
Registers) und 04 (Read Input Registers). Es gibt keinen Code, der Schreib- oder
Fahrbefehle bauen kann (siehe `request`, das alles andere ablehnt).

Was das Skript macht:
  1. serielle Ports anzeigen, USB-Adapter (FTDI) möglichst selbst finden
  2. Verbindung suchen: Baudrate x Parität x Slave-Adresse, bis der Treiber antwortet
  3. Register in einem selbst gewählten Bereich lesen, Rohwerte anzeigen, als CSV speichern
  4. Vergleich: Messung, Motor (von Hand oder per Original-Steuerung) verstellen,
     zweite Messung; geänderte Register = Kandidaten für die Ist-Position

Annahmen (nicht bestätigt, alles einstellbar):
  * Modbus RTU, 8 Datenbits, 1 Stoppbit (übliche Vorgabe; --bytesize / --stopbits)
  * Baudrate eine von 9600..115200, Parität None oder Even, Slave-Adresse 1..10
  * Für die Suche wird Register 0 gelesen (--probe-reg). Auch eine Modbus-Fehlerantwort
    ("Adresse ungültig") zählt als Treffer: dann stimmen Baudrate, Parität und Adresse.
  * Register sind 16 Bit; 32-Bit-Werte (z.B. Positionen) können über zwei Register gehen,
    der Vergleich zeigt deshalb auch Paare.
  * Keine Registeradressen sind bekannt; das Skript setzt keine voraus.

Anschluss (vorher prüfen): Motor RX <- Adapter TXD, Motor TX -> Adapter RXD, GND <-> GND,
5 V am Motorstecker frei lassen, nur mit isoliertem Adapter. Nicht unter Spannung umstecken.

    pip install pyserial
    python tools/jss57p2n_modbus_scan.py                      # geführt, mit Fragen
    python tools/jss57p2n_modbus_scan.py --list               # nur Ports anzeigen
    python tools/jss57p2n_modbus_scan.py --port /dev/ttyUSB0 --search
    python tools/jss57p2n_modbus_scan.py --port /dev/ttyUSB0 --baud 115200 --parity N --slave 1 \\
        --scan 0x0000 0x007F --func 3 --csv scan.csv
    python tools/jss57p2n_modbus_scan.py --port /dev/ttyUSB0 --baud 115200 --parity N --slave 1 \\
        --scan 0x0000 0x007F --compare
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from dataclasses import dataclass
from datetime import datetime

READ_FUNCS = {3: "Holding", 4: "Input"}    # die einzigen Funktionen, die dieses Skript sendet
BAUDS = (9600, 19200, 38400, 57600, 115200)
PARITIES = ("N", "E")
SLAVES = range(1, 11)
MAX_BLOCK = 125                            # Modbus-Grenze für Funktion 03/04
DEFAULT_BLOCK = 8
EXCEPTIONS = {1: "Funktion nicht unterstützt", 2: "Registeradresse ungültig",
              3: "Wert ungültig", 4: "Gerätefehler", 6: "Gerät beschäftigt"}


# ------------------------------------------------------------------ Modbus RTU (rein, testbar)
def crc16(data: bytes | list[int]) -> bytes:
    """Modbus CRC16 (Start 0xFFFF, Polynom 0xA001), niederwertiges Byte zuerst.
    Gleich wie in f21s_raw_test.py (Test prüft das); dort nicht importiert, weil jenes
    Modul auch Schreibbefehle enthält und dieses Skript keine kennen soll."""
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return bytes((crc & 0xFF, crc >> 8))


def request(slave: int, func: int, start: int, count: int) -> bytes:
    """Leseanfrage. Alles außer Funktion 03/04 wird abgelehnt: hier gibt es kein Schreiben."""
    if func not in READ_FUNCS:
        raise ValueError(f"Funktion {func} ist nicht erlaubt (nur lesen: 3 oder 4)")
    if not (1 <= slave <= 247 and 0 <= start <= 0xFFFF and 1 <= count <= MAX_BLOCK
            and start + count - 1 <= 0xFFFF):
        raise ValueError("Slave 1..247, Start 0..0xFFFF, Anzahl 1..125")
    data = bytes((slave, func, start >> 8, start & 0xFF, count >> 8, count & 0xFF))
    return data + crc16(data)


@dataclass
class Reply:
    values: list[int] | None = None     # Registerwerte bei Erfolg
    exception: int | None = None        # Modbus-Fehlercode (Gerät antwortet, lehnt aber ab)
    error: str | None = None            # keine/ungültige Antwort

    @property
    def answered(self) -> bool:
        """Das Gerät hat gültig geantwortet (Werte oder Fehlercode): Verbindung stimmt."""
        return self.values is not None or self.exception is not None


def parse(raw: bytes, slave: int, func: int, count: int) -> Reply:
    """Antwort prüfen: Adresse, Funktion, Länge und CRC."""
    if not raw:
        return Reply(error="keine Antwort")
    if len(raw) >= 2 and raw[0] == slave and raw[1] == func | 0x80:
        # Fehlerantwort: genau Adresse, Funktion|0x80, Fehlercode, CRC (5 Bytes), sonst kein Treffer
        if len(raw) != 5:
            return Reply(error=f"Fehlerantwort mit falscher Länge ({len(raw)} statt 5 Bytes)")
        if crc16(raw[:3]) != raw[3:5]:
            return Reply(error="Fehlerantwort mit falscher CRC")
        return Reply(exception=raw[2])
    if len(raw) < 5:
        return Reply(error=f"zu kurz ({len(raw)} Bytes): {raw.hex(' ')}")
    if raw[0] != slave:
        return Reply(error=f"falsche Slave-Adresse {raw[0]}")
    if raw[1] != func:
        return Reply(error=f"falscher Funktionscode {raw[1]}")
    need = 5 + 2 * count
    if raw[2] != 2 * count or len(raw) != need:
        return Reply(error=f"falsche Länge ({len(raw)} statt {need} Bytes)")
    if crc16(raw[:-2]) != raw[-2:]:
        return Reply(error="falsche CRC")
    return Reply(values=[int.from_bytes(raw[3 + 2 * i:5 + 2 * i], "big") for i in range(count)])


def changes(a: dict, b: dict) -> list[tuple]:
    """Register, deren Wert sich zwischen zwei Messungen geändert hat: (func, adr, alt, neu)."""
    return [(k[0], k[1], a[k], b[k]) for k in sorted(a) if k in b and a[k] != b[k]]


def signed16(v: int) -> int:
    return v - 0x10000 if v & 0x8000 else v


def signed32(v: int) -> int:
    return v - 0x1_0000_0000 if v & 0x8000_0000 else v


# ------------------------------------------------------------------ serielle Schnittstelle
class Bus:
    """Eine geöffnete Schnittstelle mit festen Parametern; sendet nur Leseanfragen."""

    def __init__(self, port, slave: int):
        self.port, self.slave = port, slave

    def read(self, func: int, start: int, count: int) -> Reply:
        frame = request(self.slave, func, start, count)
        self.port.reset_input_buffer()
        self.port.write(frame)
        head = self.port.read(3)                  # Adresse, Funktion, Bytezahl bzw. Fehlercode
        if len(head) == 3:
            rest = 2 if head[1] & 0x80 else head[2] + 2
            head += self.port.read(rest)
        time.sleep(3.5 * 11 / self.port.baudrate)  # Modbus-Pause zwischen Rahmen
        return parse(head, self.slave, func, count)


def open_port(name: str, baud: int, parity: str, bytesize: int, stopbits: float, timeout: float):
    import serial
    return serial.Serial(name, baudrate=baud, parity=parity, bytesize=bytesize, stopbits=stopbits,
                         timeout=timeout)


def list_ports() -> list:
    from serial.tools import list_ports as lp
    return sorted(lp.comports(), key=lambda p: p.device)


def guess_port(ports) -> str | None:
    """USB-Seriell-Adapter bevorzugen (Waveshare: FTDI FT232RNL)."""
    for p in ports:
        text = f"{p.description} {p.manufacturer or ''}".lower()
        if "ftdi" in text or "ft232" in text:
            return p.device
    usb = [p.device for p in ports if "USB" in p.device]
    return usb[0] if usb else None


def search(opener, bauds, parities, slaves, probe_reg: int, funcs=(3, 4), stop_first=True,
           log=print) -> list[tuple]:
    """Alle Kombinationen durchprobieren. `opener(baud, parity)` liefert einen offenen Port."""
    found = []
    for baud in bauds:
        for parity in parities:
            log(f"  {baud} Baud, Parität {parity} …")
            port = opener(baud, parity)
            try:
                for slave in slaves:
                    for func in funcs:
                        r = Bus(port, slave).read(func, probe_reg, 1)
                        if r.answered:
                            what = (f"Wert {r.values[0]}" if r.values is not None
                                    else f"Fehlercode {r.exception} ({EXCEPTIONS.get(r.exception, '?')})")
                            log(f"  TREFFER: {baud} Baud, Parität {parity}, Slave {slave}, "
                                f"Funktion {func:02d} Register 0x{probe_reg:04X}: {what}")
                            found.append((baud, parity, slave, func))
                            if stop_first:
                                return found
                            break
                        if r.error not in (None, "keine Antwort"):
                            log(f"    Slave {slave}, Funktion {func:02d}: {r.error}")
            finally:
                port.close()
    return found


def scan(bus: Bus, start: int, end: int, funcs, block: int, log=print) -> dict:
    """Register start..end lesen. Lehnt das Gerät einen Block ab, werden dessen Register
    einzeln gelesen, damit lesbare Register darin nicht verloren gehen."""
    out: dict[tuple[int, int], int] = {}
    for func in funcs:
        adr = start
        while adr <= end:
            n = min(block, end - adr + 1)
            r = bus.read(func, adr, n)
            if r.values is not None:
                out.update({(func, adr + i): v for i, v in enumerate(r.values)})
            elif n > 1 and r.exception is not None:
                for a in range(adr, adr + n):
                    s = bus.read(func, a, 1)
                    if s.values is not None:
                        out[(func, a)] = s.values[0]
            else:
                log(f"  Funktion {func:02d} 0x{adr:04X}: "
                    + (f"Fehlercode {r.exception}" if r.exception is not None else r.error))
            adr += n
    return out


def show(values: dict) -> None:
    print("  Fkt  Adresse         hex     dez   signed")
    for (func, adr), v in sorted(values.items()):
        print(f"  {func:02d}   0x{adr:04X} ({adr:5d})  {v:04X}  {v:6d}  {signed16(v):7d}")


def save_csv(path: str, rows: list[tuple]) -> None:
    """rows: (Messung, Zeit, Funktion, Adresse, Wert)."""
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["messung", "zeit", "funktion", "adresse_hex", "adresse", "wert", "wert_hex", "signed16"])
        for run, ts, func, adr, v in rows:
            w.writerow([run, ts, func, f"0x{adr:04X}", adr, v, f"0x{v:04X}", signed16(v)])
    print(f"Gespeichert: {path} ({len(rows)} Zeilen)")


def show_changes(a: dict, b: dict) -> None:
    diff = changes(a, b)
    if not diff:
        print("Keine Änderungen. Hat sich der Motor wirklich bewegt?")
        return
    print("Geänderte Register (Kandidaten für Ist-Position, Zähler, Status):")
    print("  Fkt  Adresse            vorher  nachher  Differenz(signed)")
    for func, adr, old, new in diff:
        print(f"  {func:02d}   0x{adr:04X} ({adr:5d})  {old:6d}  {new:7d}  {signed16(new) - signed16(old):+d}")
    print("Als 32-Bit-Paar gelesen (Position kann über zwei Register gehen):")
    for func, adr, _old, _new in diff:
        for lo_first, label in ((False, "hoch,tief"), (True, "tief,hoch")):
            k1, k2 = (func, adr), (func, adr + 1)
            if k2 in a and k2 in b:
                pair = (lambda d: (d[k2] << 16 | d[k1]) if lo_first else (d[k1] << 16 | d[k2]))
                print(f"  {func:02d}   0x{adr:04X}+0x{adr + 1:04X} ({label}): "
                      f"{signed32(pair(a))} -> {signed32(pair(b))}  ({signed32(pair(b)) - signed32(pair(a)):+d})")
    print("Hinweis: Ein Register, das sich auch ohne Bewegung ändert, ist eher eine Uhr oder ein "
          "Messwert. Zum Gegenprüfen die Messung ohne Bewegung wiederholen.")


# ------------------------------------------------------------------ Bedienung
def ask(text: str, default: str = "") -> str:
    v = input(f"{text}{f' [{default}]' if default else ''}: ").strip()
    return v or default


def num(text: str) -> int:
    return int(text, 0)          # 123, 0x7B, 0o173


def main() -> None:
    ap = argparse.ArgumentParser(description="JSS57P2N per RS232/Modbus RTU nur lesend untersuchen")
    ap.add_argument("--list", action="store_true", help="nur serielle Ports anzeigen")
    ap.add_argument("--port", help="z.B. /dev/ttyUSB0 (sonst Auswahl)")
    ap.add_argument("--search", action="store_true", help="Baudrate, Parität und Slave suchen")
    ap.add_argument("--all", action="store_true", help="bei der Suche nicht beim ersten Treffer aufhören")
    ap.add_argument("--baud", type=int, help="feste Baudrate (statt Suche)")
    ap.add_argument("--parity", choices=("N", "E", "O"), help="feste Parität")
    ap.add_argument("--bytesize", type=int, default=8, choices=(7, 8))
    ap.add_argument("--stopbits", type=float, default=1, choices=(1, 1.5, 2))
    ap.add_argument("--slave", type=int, help="feste Slave-Adresse")
    ap.add_argument("--slaves", default="1-10", help="Suchbereich Slave, z.B. 1-10")
    ap.add_argument("--probe-reg", type=num, default=0, help="Register für die Suche (Standard 0)")
    ap.add_argument("--timeout", type=float, default=0.2, help="Sekunden pro Antwort")
    ap.add_argument("--scan", nargs=2, type=num, metavar=("START", "ENDE"), help="Registerbereich, z.B. 0 0x7F")
    ap.add_argument("--func", choices=("3", "4", "both"), default="both", help="03, 04 oder beide")
    ap.add_argument("--block", type=int, default=DEFAULT_BLOCK, help=f"Register pro Anfrage (1..{MAX_BLOCK})")
    ap.add_argument("--repeat", type=int, default=1, help="Messungen wiederholen")
    ap.add_argument("--interval", type=float, default=1.0, help="Sekunden zwischen Wiederholungen")
    ap.add_argument("--csv", help="Ergebnis als CSV speichern")
    ap.add_argument("--compare", action="store_true", help="zwei Messungen mit Verstellen dazwischen vergleichen")
    a = ap.parse_args()
    if not 1 <= a.block <= MAX_BLOCK:
        sys.exit(f"--block muss 1..{MAX_BLOCK} sein")
    try:
        import serial  # noqa: F401
    except ImportError:
        sys.exit("pyserial fehlt: pip install pyserial")

    try:
        ports = list_ports()
        print("Serielle Ports:")
        for p in ports:
            print(f"  {p.device:15s} {p.description}")
        if not ports:
            print("  (keine gefunden – Adapter eingesteckt? Rechte: Gruppe 'dialout')")
        if a.list:
            return
        port = a.port or ask("Port", guess_port(ports) or "/dev/ttyUSB0")
        lo, hi = (int(x) for x in a.slaves.split("-"))
        settings = None
        if a.baud and a.parity and a.slave:
            settings = (a.baud, a.parity, a.slave)
        elif a.search or ask("Verbindung suchen? (j/n)", "j").lower().startswith("j"):
            print(f"Suche (nur Leseanfragen, Register 0x{a.probe_reg:04X}, {a.bytesize} Datenbits, "
                  f"{a.stopbits:g} Stoppbit) …")
            found = search(lambda b, p: open_port(port, b, p, a.bytesize, a.stopbits, a.timeout),
                           [a.baud] if a.baud else BAUDS, [a.parity] if a.parity else PARITIES,
                           [a.slave] if a.slave else range(lo, hi + 1), a.probe_reg,
                           stop_first=not a.all)
            if not found:
                sys.exit("Keine Antwort. Verkabelung (TX/RX getauscht?), Masse, Treiber eingeschaltet? "
                         "Andere Datenbits/Stoppbits/--probe-reg probieren.")
            settings = found[0][:3]
        else:
            settings = (int(ask("Baudrate", "115200")), ask("Parität N/E", "N").upper(), int(ask("Slave", "1")))
        baud, parity, slave = settings
        print(f"Verbindung: {port}, {baud} Baud, {a.bytesize}{parity}{a.stopbits:g}, Slave {slave}")
        if not a.scan:
            if not ask("Register lesen? (j/n)", "j").lower().startswith("j"):
                return
            a.scan = (num(ask("Startadresse", "0x0000")), num(ask("Endadresse", "0x007F")))
            if not a.compare:
                a.compare = ask("Vergleich mit Verstellen dazwischen? (j/n)", "n").lower().startswith("j")
            if not a.csv:
                a.csv = ask("CSV-Datei (leer = nicht speichern)", "") or None
        start, end = a.scan
        if end < start:
            sys.exit("Endadresse vor Startadresse")
        funcs = (3, 4) if a.func == "both" else (int(a.func),)
        ser = open_port(port, baud, parity, a.bytesize, a.stopbits, a.timeout)
        bus, rows = Bus(ser, slave), []
        try:
            runs = 2 if a.compare else a.repeat
            results = []
            for run in range(1, runs + 1):
                if a.compare and run == 2:
                    input("Jetzt die Motorstellung verändern, warten bis er steht, dann Enter … ")
                print(f"Messung {run}: Register 0x{start:04X}..0x{end:04X}, Funktion "
                      + "/".join(f"{f:02d}" for f in funcs))
                ts = datetime.now().isoformat(timespec="milliseconds")
                values = scan(bus, start, end, funcs, a.block)
                results.append(values)
                rows += [(run, ts, f, adr, v) for (f, adr), v in sorted(values.items())]
                if run == 1 or not a.compare:
                    show(values)
                if not values:
                    print("  Kein Register lesbar in diesem Bereich.")
                if run < runs and not a.compare:
                    time.sleep(a.interval)
            if a.compare and len(results) == 2:
                show_changes(*results)
            elif len(results) > 1:
                show_changes(results[0], results[-1])
        finally:
            ser.close()
        if a.csv:
            save_csv(a.csv, rows)
    except KeyboardInterrupt:
        print("\nAbgebrochen.")
    except OSError as e:
        sys.exit(f"Schnittstelle: {e} (Port richtig? Rechte: sudo usermod -aG dialout $USER, neu anmelden)")


if __name__ == "__main__":
    main()
