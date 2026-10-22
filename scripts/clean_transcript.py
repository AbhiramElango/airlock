"""Render terminal edits (backspace, CSI insert/delete), drop control codes, redact. stdin -> stdout."""
import re
import sys

from airlock.audit import redact

CSI = re.compile(r"\x1b\[([0-9;?]*)([@-~])")
OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")


def render(line: str) -> str:
    buf: list[str] = []
    cur = i = 0
    while i < len(line):
        m = CSI.match(line, i) or OSC.match(line, i)
        if m:
            if m.re is CSI:
                n = int(m.group(1)) if m.group(1).isdigit() else 1
                final = m.group(2)
                if final == "@":
                    buf[cur:cur] = [" "] * n
                elif final == "P":
                    del buf[cur:cur + n]
                elif final == "K":
                    del buf[cur:]
                elif final == "C":
                    cur += n
                elif final == "D":
                    cur = max(0, cur - n)
            i = m.end()
            continue
        c = line[i]
        if c == "\b":
            cur = max(0, cur - 1)
        elif c == "\r":
            cur = 0
        elif c >= " ":
            buf.extend(" " * (cur - len(buf)))
            if cur < len(buf):
                buf[cur] = c
            else:
                buf.append(c)
            cur += 1
        i += 1
    return "".join(buf).rstrip()


if __name__ == "__main__":
    text = "\n".join(render(l) for l in sys.stdin.read().split("\n"))
    sys.stdout.write(redact(text))
