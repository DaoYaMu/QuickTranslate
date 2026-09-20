"""划词取词回归测试：python tests/test_selection.py

锁住一条实测结论 —— **注入 Ctrl+C 之前必须等热键的修饰键松开**。

热键回调是在按下主键的瞬间触发的，那时用户通常还按着 Alt/Ctrl/Shift；
若立刻注入 Ctrl+C，系统会把它合成成 ``Ctrl+Alt+Shift+C``，目标程序不认，
表现就是「划词翻译没反应」。实测（独立进程窗口为目标）：

    注入时未按修饰键        0.05s  取到文本
    注入时按住 Alt+Shift    超时   取不到

本测试用打桩替代真实按键与剪贴板，因此不会动用户的剪贴板。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quicktranslate.core import clipboard  # noqa: E402

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if ok else 'FAIL'} {name}" + (f"  {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


class FakeClipboard:
    """内存剪贴板，避免测试动到真实剪贴板。"""

    def __init__(self) -> None:
        self.value = ""

    def get(self) -> str:
        return self.value

    def set(self, text: str) -> None:
        self.value = text


def patch_modifiers(sequence: list[list[str]]):
    """按调用次序返回修饰键状态，用完后固定返回最后一组。"""
    state = {"i": 0}

    def _fake() -> list[str]:
        i = min(state["i"], len(sequence) - 1)
        state["i"] += 1
        return list(sequence[i])

    return _fake


def main() -> int:
    real_get, real_set = clipboard.get_clipboard_text, clipboard.set_clipboard_text
    real_pressed, real_inject = clipboard.pressed_modifiers, clipboard._inject_copy
    real_wait = clipboard.wait_modifier_release

    try:
        # --- 1. 真实环境下的修饰键查询 -------------------------------- #
        held = clipboard.pressed_modifiers()
        check("pressed_modifiers 可调用且返回列表", isinstance(held, list), repr(held))
        started = time.monotonic()
        released = clipboard.wait_modifier_release(timeout=0.3)
        spent = time.monotonic() - started
        check(
            "当前未按修饰键时应立刻返回",
            released and spent < 0.1,
            f"返回 {released}，用时 {spent:.3f}s",
        )

        # --- 2. 一直按着 → 等到超时后放弃 ------------------------------ #
        clipboard.pressed_modifiers = lambda: ["左 Alt", "左 Shift"]
        started = time.monotonic()
        released = clipboard.wait_modifier_release(timeout=0.2, poll=0.02)
        spent = time.monotonic() - started
        check(
            "修饰键一直按着时等待超时并返回 False",
            (not released) and 0.15 <= spent < 0.6,
            f"返回 {released}，用时 {spent:.3f}s",
        )

        # --- 3. 稍后松开 → 等到松开为止 -------------------------------- #
        clipboard.pressed_modifiers = patch_modifiers(
            [["左 Alt", "左 Shift"], ["左 Alt", "左 Shift"], [], []]
        )
        started = time.monotonic()
        released = clipboard.wait_modifier_release(timeout=1.0, poll=0.02)
        spent = time.monotonic() - started
        check(
            "修饰键稍后松开时能等到并返回 True",
            released and spent >= 0.03,
            f"返回 {released}，用时 {spent:.3f}s",
        )

        # --- 4. 核心保证：注入必须发生在修饰键松开之后 ------------------ #
        clipboard.pressed_modifiers = patch_modifiers(
            [["左 Alt", "左 Shift"], ["左 Alt", "左 Shift"], []]
        )
        snapshot: list[list[str]] = []

        def fake_inject() -> bool:
            snapshot.append(list(clipboard.pressed_modifiers()))
            return True

        clipboard._inject_copy = fake_inject
        clipboard.simulate_copy()
        check(
            "注入 Ctrl+C 时修饰键已松开",
            bool(snapshot) and not snapshot[0],
            f"注入瞬间的修饰键={snapshot}",
        )

        # --- 5. 取词成功：拿到文本，且剪贴板留下的是新内容 -------------- #
        clip = FakeClipboard()
        clipboard.get_clipboard_text = clip.get
        clipboard.set_clipboard_text = clip.set
        clipboard.pressed_modifiers = patch_modifiers([[], []])
        clipboard._inject_copy = lambda: (clip.set("选中的文字 selected"), True)[1]
        check(
            "取词成功时返回文本",
            clipboard.get_selected_text() == "选中的文字 selected",
        )

        # --- 6. 取词失败：不重试，且剪贴板要还原 ------------------------ #
        clip.value = "用户原本的剪贴板内容"
        clipboard.pressed_modifiers = lambda: ["左 Alt"]
        clipboard._inject_copy = lambda: True  # 不往剪贴板放东西
        waits = {"n": 0}

        def counting_wait(timeout: float = 0.8, poll: float = 0.02) -> bool:
            waits["n"] += 1
            return False  # 模拟"用户一直按着修饰键"

        clipboard.wait_modifier_release = counting_wait
        empty = clipboard.get_selected_text(timeout=0.15)
        check(
            "一直按着修饰键时只等一次、不重试",
            empty == "" and waits["n"] == 1,
            f"等待被调用 {waits['n']} 次",
        )
        check(
            "取词失败后剪贴板还原为原内容",
            clip.value == "用户原本的剪贴板内容",
            repr(clip.value),
        )
        clipboard.wait_modifier_release = real_wait

        # --- 7. 失败原因要可读，且能指出"修饰键还按着" ------------------ #
        text, reason = clipboard.grab_selection(timeout=0.15)
        check(
            "失败原因指出修饰键没松开",
            text == "" and "松开" in reason,
            repr(reason),
        )
        clipboard.pressed_modifiers = lambda: []
        _, reason = clipboard.grab_selection(timeout=0.15)
        check("未按修饰键时的原因指向'没有选中的文本'", "选中" in reason, repr(reason))
    finally:
        clipboard.get_clipboard_text = real_get
        clipboard.set_clipboard_text = real_set
        clipboard.pressed_modifiers = real_pressed
        clipboard._inject_copy = real_inject
        clipboard.wait_modifier_release = real_wait

    print()
    if FAILURES:
        print("test_selection: FAIL ->", "、".join(FAILURES))
        return 1
    print("test_selection: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
