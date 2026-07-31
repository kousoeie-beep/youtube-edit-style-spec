"""styles/*.yaml を安全に編集するヘルパー。

【なぜ必要か】2026-07-27 に**同じ重複キー事故を8回作った**。
原因は毎回同じで、role ブロックに新しいフィールドを挿入するとき
**既存の同名キーを確認していなかった**こと。YAMLは後勝ちなので、
先に書いた値（＝たいてい今回入れた修正値）が黙って消える。

このモジュールを通せば、同名キーがあれば置換、無ければ挿入になる。
"""
import io
import re


def _block_range(text, role, occurrence=0):
    """role ブロックの [start, end) を返す。"""
    ms = [m for m in re.finditer(r'^  - role: ([a-z_]+)\b', text, re.M)]
    idxs = [i for i, m in enumerate(ms) if m.group(1) == role]
    if not idxs:
        raise KeyError(f"role が見つからない: {role}")
    i = idxs[occurrence]
    st = ms[i].start()
    en = ms[i + 1].start() if i + 1 < len(ms) else len(text)
    nxt = re.search(r'^\w', text[st:en], re.M)
    if nxt:
        en = st + nxt.start()
    return st, en


def set_field(path, role, key, value, note="", occurrence=0):
    """role の key を value にする。既存があれば置換、無ければ position の前に挿入。

    **重複キーを作らないことを保証する**のがこの関数の唯一の存在理由。
    """
    s = io.open(path, encoding="utf-8").read()
    st, en = _block_range(s, role, occurrence)
    blk = s[st:en]
    line = f"    {key}: {value}"
    if note:
        line += f"   # {note}"

    m = re.search(rf'^(\s+){re.escape(key)}:.*$', blk, re.M)
    if m:
        # 既存行を置換する。継続コメント行はそのまま残す
        blk2 = blk[:m.start()] + line + blk[m.end():]
        action = "置換"
    else:
        pm = re.search(r'^    position: ', blk, re.M)
        if not pm:
            raise KeyError(f"position 行が見つからない: {role}")
        blk2 = blk[:pm.start()] + line + "\n" + blk[pm.start():]
        action = "挿入"
    io.open(path, "w", encoding="utf-8").write(s[:st] + blk2 + s[en:])
    return action


def drop_field(path, role, key, replace_with_note, occurrence=0):
    """role の key 行とその継続コメントを削除し、注記へ置き換える。"""
    s = io.open(path, encoding="utf-8").read()
    st, en = _block_range(s, role, occurrence)
    blk = s[st:en]
    lines = blk.split("\n")
    out, i, dropped = [], 0, 0
    while i < len(lines):
        m = re.match(rf'^(\s+){re.escape(key)}:', lines[i])
        if not m:
            out.append(lines[i]); i += 1; continue
        i += 1
        while i < len(lines) and re.match(r'^\s+#', lines[i]):
            i += 1
        out.append(f"    # {replace_with_note}")
        dropped += 1
    io.open(path, "w", encoding="utf-8").write(s[:st] + "\n".join(out) + s[en:])
    return dropped
