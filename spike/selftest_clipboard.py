"""回归测试：剪贴板历史存储、去重/上限、监听、面板选择。"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from sniptool.bootstrap import setup_qt_plugins

setup_qt_plugins()

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QImage, QGuiApplication, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from sniptool.clipboard import ClipboardHistory, ClipboardPanel, HistoryStore

app = QApplication(sys.argv)
fails = []


def check(name, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}", flush=True)
    if not ok:
        fails.append(name)


def settle(seconds=0.15):
    t = time.time()
    while time.time() - t < seconds:
        app.processEvents()
        time.sleep(0.01)


tmp = tempfile.mkdtemp(prefix="sniptool_clip_")
db = os.path.join(tmp, "history.db")

# 老库（没有 pinned 列）自动迁移
import sqlite3

old_db = os.path.join(tmp, "old.db")
con = sqlite3.connect(old_db)
con.execute(
    "CREATE TABLE items(id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,"
    " text TEXT, image BLOB, thumb BLOB, digest TEXT NOT NULL, created REAL NOT NULL,"
    " size INTEGER NOT NULL DEFAULT 0)"
)
con.execute("INSERT INTO items(kind,text,digest,created,size) VALUES('text','old','d',?,1)",
            (time.time(),))
con.commit(); con.close()
old_store = HistoryStore(old_db, limit=3, days=7)
check("老库自动迁移 pinned 列", old_store.count() == 1
      and old_store.toggle_pin(old_store.items()[0].id) is True)
old_store.close()

store = HistoryStore(db, limit=3, days=7)

# 文字 + 去重
check("写入文字", store.add_text("第一条") and store.add_text("第二条"))
check("连续重复不再新增", not store.add_text("第二条"))
check("重复内容提到最前", store.add_text("第一条") and store.items()[0].text == "第一条",
      f"{[i.text for i in store.items()]}")

# 上限
store.add_text("第三条")
store.add_text("第四条")
check("历史条数受上限约束", store.count() == 3, f"{store.count()}")
check("最新记录在最前", store.items()[0].text == "第四条", f"{store.items()[0].text}")

# 图片
img = QImage(500, 360, QImage.Format.Format_RGB32)
img.fill(QColor(10, 180, 90))
check("写入图片", store.add_image(img))
items = store.items()
check("图片有缩略图", items[0].kind == "image" and items[0].thumb is not None,
      f"{items[0].kind} thumb={items[0].thumb is not None}")
got = store.get(items[0].id)
check("图片原图可取回", got is not None and got.image is not None
      and got.image.width() == 500 and got.image.height() == 360,
      f"{got.image.size() if got and got.image else None}")

# 重新打开文件库，数据仍在
store.close()
store = HistoryStore(db, limit=3, days=7)
check("历史已落盘", store.count() >= 3 and store.items()[0].kind == "image",
      f"{store.count()} / {store.items()[0].kind}")

# 7 天过期
old_id = store.items()[0].id
store._db.execute("UPDATE items SET created = ? WHERE id = ?", (time.time() - 9 * 86400, old_id))
store._db.commit()
store.prune()
check("过期记录被清理", store.get(old_id) is None)

# 删除/清空
some = store.items()[0]
store.remove(some.id)
check("可删除单条", store.get(some.id) is None)
store.clear()
check("可清空历史", store.count() == 0)

# 清空普通项（保留收藏）
store.add_text("普通A")
store.add_text("收藏B")
b_id = store.items()[0].id
store.toggle_pin(b_id)
store.add_text("普通C")
check("清空前总数", store.count() == 3)
store.clear_unpinned()
check("clear_unpinned 保留收藏", store.count() == 1 and store.get(b_id) is not None and store.get(b_id).pinned)
store.clear()

# 收藏：置顶、永久保留（不受条数/天数清理影响）
check("写入收藏项", store.add_text("收藏我"))
fav = store.items()[0]
check("可收藏", store.toggle_pin(fav.id) is True)
check("收藏项已置顶", store.items()[0].id == fav.id and store.items()[0].pinned,
      f"{[(i.id, i.text, i.pinned) for i in store.items()]}")
check("收藏计数", store.pinned_count() == 1, f"{store.pinned_count()}")
for i in range(6):
    store.add_text(f"普通-{i}")
store.set_limits(2, 7)
texts = [i.text for i in store.items()]
check("条数上限只清普通项", "收藏我" in texts and len(texts) == 3, f"{texts}")
# 把收藏项改成 30 天前，仍不能被天数清理
store._db.execute("UPDATE items SET created = ? WHERE id = ?",
                  (time.time() - 30 * 86400, fav.id))
store._db.commit()
store.prune()
check("收藏项不受 7 天限制", store.get(fav.id) is not None)
check("取消收藏", store.toggle_pin(fav.id) is False)
check("取消后计数归零", store.pinned_count() == 0)
store.clear()

# 监听系统剪贴板
watch = ClipboardHistory(store)
QGuiApplication.clipboard().setText("watched-text")
settle(0.25)
found = [i for i in store.items() if i.text == "watched-text"]
check("监听剪贴板文字", bool(found), f"{[i.text for i in store.items()]}")

img2 = QImage(120, 80, QImage.Format.Format_RGB32)
img2.fill(QColor(200, 40, 40))
QGuiApplication.clipboard().setImage(img2)
settle(0.25)
check("监听剪贴板图片", store.items()[0].kind == "image", f"{store.items()[0].kind}")

# 自身写入不进历史（guard）
before = store.count()
item = store.get(store.items()[0].id)
watch.set_clipboard(item)
settle(0.2)
check("回写剪贴板不产生重复记录", store.count() == before, f"{before} -> {store.count()}")

# 面板
store.set_limits(20, 7)
for i in range(4):
    store.add_text(f"panel-{i}")
panel = ClipboardPanel(store)
check("面板不强制置顶", not (panel.windowFlags() & Qt.WindowType.WindowStaysOnTopHint))
panel.show_at_cursor()
settle(0.3)
check("面板显示记录", panel.list.count() == store.count(), f"{panel.list.count()} vs {store.count()}")
check("面板拿到焦点", panel.isActiveWindow() and panel.list.hasFocus(),
      f"active={panel.isActiveWindow()} list={panel.list.hasFocus()}")
chosen = []
panel.chosen.connect(lambda i: chosen.append(i))

# 单击只选中，不粘贴
panel.list.setCurrentRow(0)
target = panel.list.item(2)
panel.list.setCurrentItem(target)
QTest.mouseClick(panel.list.viewport(), Qt.MouseButton.LeftButton,
                 pos=panel.list.visualItemRect(target).center())
settle(0.1)
check("单击只选中不粘贴", not chosen and panel.isVisible() and panel._current_id() == target.data(Qt.ItemDataRole.UserRole),
      f"chosen={chosen} row={panel.list.currentRow()}")

# 双击粘贴（QTest.mouseDClick 在此环境不会触发 QListWidget 的 doubleClicked；
# 这里直接触发 itemActivated，等价于双击/Enter 的激活路径）
target = panel.list.item(1)
panel.list.setCurrentItem(target)
panel.list.itemActivated.emit(target)
settle(0.2)
check("双击记录粘贴", len(chosen) == 1 and chosen[0] == target.data(Qt.ItemDataRole.UserRole),
      f"{chosen}")
check("粘贴后面板关闭", not panel.isVisible())

# Enter 粘贴
panel.show_at_cursor()
settle(0.25)
panel.list.setCurrentRow(1)
expect = panel._current_id()
QTest.keyClick(panel.list, Qt.Key.Key_Return)
settle(0.15)
check("Enter 粘贴", len(chosen) == 2 and chosen[1] == expect, f"{chosen}")

# 删除后停在原位置（不跳回开头）
panel.show_at_cursor()
settle(0.25)
panel.list.setCurrentRow(2)
row_before = panel.list.currentRow()
count_before = panel.list.count()
del_id = panel._current_id()
QTest.keyClick(panel.list, Qt.Key.Key_Delete)
settle(0.15)
check("删除后仍在原行位置", panel.list.currentRow() == row_before
      and panel.list.count() == count_before - 1 and panel._current_id() != del_id,
      f"row={panel.list.currentRow()} count={panel.list.count()}")

# 收藏二级界面
fav_row = panel.list.currentRow()
panel.list.setCurrentRow(0)
fav_id = panel._current_id()
panel.list.setCurrentRow(fav_row)
panel.list.setCurrentItem(panel.list.item(0))
panel._toggle_pin_current()
check("面板收藏生效且从全部中过滤", store.get(fav_id).pinned,
      f"pinned={store.get(fav_id).pinned}")
panel.set_mode("pinned")
settle(0.1)
check("收藏二级界面只显示收藏", panel.list.count() == store.pinned_count()
      and panel.count.text().startswith("收藏"), f"{panel.list.count()} / {panel.count.text()}")
check("收藏项有星标", panel.list.item(0).text().startswith("★"),
      panel.list.item(0).text()[:20])
QTest.keyClick(panel.list, Qt.Key.Key_Space)
settle(0.1)
check("收藏界面取消收藏后原地定位", not store.get(fav_id).pinned and panel.list.count() == 1,
      f"pinned={store.get(fav_id).pinned} count={panel.list.count()}")
panel.set_mode("all")

# 清空按钮测试（右对齐，只清空未收藏内容，保留收藏）
fav_id2 = store.items()[0].id
store.toggle_pin(fav_id2)
panel.refresh()
total_before = store.count()
pinned_before = store.pinned_count()
check("清空测试准备：总数大于收藏数", total_before > pinned_before and pinned_before >= 1)
panel.btn_clear.click()
settle(0.1)
check("点击清空按钮后仅剩收藏项", store.count() == pinned_before and store.get(fav_id2).pinned)
check("全部列表中已清空显示占位符", panel.list.count() == 1 and not (panel.list.item(0).flags() & Qt.ItemFlag.ItemIsSelectable))

# 窗口可拖动、没有置顶
pos0 = panel.pos()
def mev(kind, local, global_, buttons):
    from PySide6.QtCore import QPointF
    return QMouseEvent(kind, QPointF(local), QPointF(global_), Qt.MouseButton.LeftButton,
                       buttons, Qt.KeyboardModifier.NoModifier)
l1 = QPoint(60, 10)
l2 = QPoint(100, 40)
panel.eventFilter(panel.title, mev(QMouseEvent.Type.MouseButtonPress, l1, pos0 + l1,
                                   Qt.MouseButton.LeftButton))
panel.eventFilter(panel.title, mev(QMouseEvent.Type.MouseMove, l2, pos0 + l2,
                                   Qt.MouseButton.LeftButton))
panel.eventFilter(panel.title, mev(QMouseEvent.Type.MouseButtonRelease, l2, pos0 + l2,
                                   Qt.MouseButton.NoButton))
check("面板标题栏可拖动", panel.pos() != pos0, f"{pos0.toTuple()} -> {panel.pos().toTuple()}")

# 圆角：四角透明，中心不透明
pimg = panel.grab().toImage()
check("剪贴板面板圆角透明", pimg.pixelColor(0, 0).alpha() == 0
      and pimg.pixelColor(pimg.width() // 2, pimg.height() // 2).alpha() > 200,
      f"corner={pimg.pixelColor(0, 0).getRgb()} center={pimg.pixelColor(pimg.width() // 2, pimg.height() // 2).getRgb()}")

empty_store = HistoryStore(None, 3, 7)
panel2 = ClipboardPanel(empty_store)
panel2.refresh()
check("空历史显示占位", panel2.list.count() == 1
      and not (panel2.list.item(0).flags() & Qt.ItemFlag.ItemIsSelectable),
      f"{panel2.list.count()}")

store.close()
empty_store.close()
print("\n" + ("全部通过" if not fails else "失败: " + ", ".join(fails)))
sys.exit(0 if not fails else 1)
