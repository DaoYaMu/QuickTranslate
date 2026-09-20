"""剪贴板与划词取词。

划词取词的原理是「清空剪贴板 → 模拟 Ctrl+C → 读剪贴板」。

这里有一个必须处理的陷阱：**热键回调是在按下主键的瞬间触发的，此时用户
通常还按着热键的修饰键**。注入的 Ctrl+C 会与物理按下的修饰键合成为
``Ctrl+Shift+C`` / ``Ctrl+Alt+Shift+C``，而绝大多数程序不把它当"复制"，
于是表现为「划词翻译没反应」。

实测（记事本/独立 Qt 窗口为目标，热键 Alt+Shift+T）：

    注入时未按修饰键        0.05s  取到正确文本
    注入时按住 Shift        超时   取不到
    注入时按住 Alt+Shift    超时   取不到   ← 热键按下瞬间的真实状态
    注入时按住 Ctrl+Shift   超时   取不到

所以注入前必须**等修饰键全部松开**，见 :func:`wait_modifier_release`。
"""

from __future__ import annotations

import ctypes
import sys
import time

from ..utils.logging import get_logger

log = get_logger("clipboard")

# 等修饰键松开的上限（秒）。热键按下后用户一般在 0.3s 内松手。
_MODIFIER_WAIT = 0.8
# 注入后等剪贴板变化的时长（秒）。
_CLIPBOARD_WAIT = 0.5

# 左右分键的虚拟键码，便于把"到底哪个键还按着"说清楚
_WIN_MODIFIER_VKS: tuple[tuple[int, str], ...] = (
    (0xA0, "左 Shift"),
    (0xA1, "右 Shift"),
    (0xA2, "左 Ctrl"),
    (0xA3, "右 Ctrl"),
    (0xA4, "左 Alt"),
    (0xA5, "右 Alt"),
    (0x5B, "左 Win"),
    (0x5C, "右 Win"),
)

# macOS 的修饰键标志位（CGEventFlags）
_MAC_MODIFIER_FLAGS: tuple[tuple[int, str], ...] = (
    (0x00020000, "Shift"),
    (0x00040000, "Ctrl"),
    (0x00080000, "Alt"),
    (0x00100000, "Cmd"),
)


def get_clipboard_text() -> str:
    try:
        import pyperclip  # noqa: PLC0415

        return pyperclip.paste() or ""
    except Exception as exc:  # noqa: BLE001
        log.warning("读取剪贴板失败：%s", exc)
        return ""


def set_clipboard_text(text: str) -> None:
    try:
        import pyperclip  # noqa: PLC0415

        pyperclip.copy(text)
    except Exception as exc:  # noqa: BLE001
        log.warning("写入剪贴板失败：%s", exc)


# --------------------------------------------------------------------------- #
# 修饰键状态
# --------------------------------------------------------------------------- #
def pressed_modifiers() -> list[str]:
    """返回当前仍被按住的修饰键（Windows / macOS 可精确查询，其他平台为空）。"""
    if sys.platform == "win32":
        try:
            get_state = ctypes.windll.user32.GetAsyncKeyState
        except Exception:  # noqa: BLE001
            return []
        held: list[str] = []
        for vk, name in _WIN_MODIFIER_VKS:
            try:
                if get_state(vk) & 0x8000:
                    held.append(name)
            except Exception:  # noqa: BLE001
                return held
        return held

    if sys.platform == "darwin":
        try:
            from Quartz import CGEventSourceFlagsState  # noqa: PLC0415

            flags = CGEventSourceFlagsState(0)  # combined session state
        except Exception:  # noqa: BLE001
            return []
        return [name for bit, name in _MAC_MODIFIER_FLAGS if flags & bit]

    return []


def wait_modifier_release(
    timeout: float = _MODIFIER_WAIT, poll: float = 0.02
) -> bool:
    """等修饰键全部松开，返回是否已松开。

    无法查询修饰键状态的平台（Linux）退化为一个固定的小延时，
    至少避开"按下热键的同一瞬间"。
    """
    if sys.platform not in ("win32", "darwin"):
        time.sleep(0.12)
        return True

    deadline = time.monotonic() + timeout
    while True:
        if not pressed_modifiers():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(poll)


# --------------------------------------------------------------------------- #
# 模拟复制
# --------------------------------------------------------------------------- #
def _copy_modifier():  # type: ignore[no-untyped-def]
    from pynput.keyboard import Key  # noqa: PLC0415

    return Key.cmd if sys.platform == "darwin" else Key.ctrl


def _inject_copy() -> bool:
    """真正注入一次复制快捷键（独立出来便于测试替换）。"""
    try:
        from pynput.keyboard import Controller  # noqa: PLC0415
    except ImportError:
        log.error("未安装 pynput，无法模拟复制")
        return False
    try:
        keyboard = Controller()
        with keyboard.pressed(_copy_modifier()):
            keyboard.press("c")
            keyboard.release("c")
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("模拟复制失败：%s", exc)
        return False


def simulate_copy(wait_release: bool = True) -> bool:
    """模拟一次复制快捷键（Windows/Linux 为 Ctrl+C，macOS 为 Cmd+C）。

    注入前会等热键的修饰键松开：否则注入的 Ctrl+C 被系统合成为
    ``Ctrl+Shift+C``，目标程序不认（"划词没反应"的头号原因）。
    """
    if wait_release and not wait_modifier_release():
        held = "、".join(pressed_modifiers()) or "修饰键"
        log.warning("修饰键（%s）一直没松开，仍尝试复制", held)
    return _inject_copy()


# --------------------------------------------------------------------------- #
# 取词
# --------------------------------------------------------------------------- #
def _poll_clipboard(timeout: float) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        text = get_clipboard_text()
        if text:
            return text
        time.sleep(0.03)
    return ""


def get_selected_text(timeout: float = _CLIPBOARD_WAIT, attempts: int = 2) -> str:
    """取当前选中文本：等修饰键松开 → 清空剪贴板 → 模拟复制 → 轮询剪贴板。

    失败时把剪贴板还原成原内容，避免把用户的剪贴板清空。
    """
    backup = get_clipboard_text()
    for index in range(max(attempts, 1)):
        # 关键：注入前必须等修饰键松开（热键回调是在按下主键的瞬间触发的）
        if not wait_modifier_release():
            held = "、".join(pressed_modifiers()) or "修饰键"
            log.warning("修饰键（%s）一直没松开，放弃取词", held)
            break  # 用户还按着，重试也没意义

        set_clipboard_text("")
        if not _inject_copy():
            break
        text = _poll_clipboard(timeout)
        if text:
            return text
        # 修饰键已松开却空手而归：多半是目标程序还没处理完，值得再来一次
        log.info("第 %d 次取词没拿到内容", index + 1)

    if backup:
        set_clipboard_text(backup)
    return ""


def grab_selection(timeout: float = _CLIPBOARD_WAIT) -> tuple[str, str]:
    """取当前选中文本，并给出可操作的失败原因。

    返回 ``(文本, 原因)``；成功时原因为空串。
    """
    text = get_selected_text(timeout)
    if text.strip():
        return text, ""

    try:
        import pynput  # noqa: F401, PLC0415
    except ImportError:
        return "", "缺少 pynput 组件，无法模拟复制快捷键"

    held = pressed_modifiers()
    if held:
        return (
            "",
            f"复制时仍按着 {'、'.join(held)}，请把热键的修饰键完全松开后再试",
        )
    return "", "没有检测到选中的文本"
