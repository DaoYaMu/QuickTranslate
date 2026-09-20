"""历史记录存储测试：python tests/test_history.py"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quicktranslate.storage.history import HistoryStore  # noqa: E402


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "history.db")
        store = HistoryStore(db)

        assert store.count() == 0, "初始应为空"
        store.add("Hello world", "你好，世界", "英语", "中文", "test")
        store.add("Good morning", "早上好", "英语", "中文", "test")
        store.add("苹果", "apple", "中文", "英语", "test")
        assert store.count() == 3, f"应写入 3 条，实际 {store.count()}"

        items = store.query()
        assert len(items) == 3
        assert items[0].src_text == "苹果", "应按 id 倒序"

        # 全文检索
        hit = store.query(keyword="morning")
        assert len(hit) == 1 and hit[0].src_text == "Good morning", "FTS 检索失败"

        hit_cn = store.query(keyword="苹果")
        assert len(hit_cn) == 1, "中文检索失败"

        # 收藏
        target = store.query(keyword="morning")[0]
        store.toggle_favorite(target.id)
        favs = store.query(only_favorite=True)
        assert len(favs) == 1 and favs[0].id == target.id, "收藏筛选失败"

        # 删除
        store.delete(target.id)
        assert store.count() == 2, "删除失败"

        store.clear()
        assert store.count() == 0, "清空失败"

        store.close()

    print("test_history: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
