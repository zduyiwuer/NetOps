"""左侧导航栏验证脚本。

覆盖：二级菜单分组结构、单个分组折叠/展开、全部展开收起、搜索过滤、
折叠态（侧边栏收起）、滚动可达性、菜单跳转。

运行：python -m scripts.verify_sidebar（需先启动服务）
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8900"
PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
results: list[tuple[str, bool, str]] = []

EXPECTED_GROUPS = ["概览", "设备管理", "流量分析", "运维监控", "资产组织", "系统与智能"]
EXPECTED_ITEM_COUNT = 18


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, cond, detail))
    print(f"  [{PASS if cond else FAIL}] {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    print("\n=== 左侧导航栏验证 ===\n")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1600, "height": 900})
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        page.goto(f"{BASE}/#/login", wait_until="networkidle")
        page.fill('input[placeholder*="用户名"]', "admin")
        page.fill('input[placeholder="密码"]', "admin123")
        page.click("button:has-text('登 录')")
        page.wait_for_timeout(2600)
        check("登录成功", "dashboard" in page.url, page.url)

        print("\n[1] 二级菜单分组结构")
        groups = page.eval_on_selector_all(
            ".el-sub-menu__title", "els => els.map(e => e.textContent.trim())"
        )
        check("二级菜单分组为 6 个", len(groups) == 6, f"{len(groups)} 个")
        check("分组名称与顺序正确", groups == EXPECTED_GROUPS, str(groups))
        items = page.eval_on_selector_all(".el-menu-item", "els => els.length")
        check(f"菜单项共 {EXPECTED_ITEM_COUNT} 个", items == EXPECTED_ITEM_COUNT, f"{items} 个")

        print("\n[2] 单个分组可折叠")
        # 找到「运维监控」分组的箭头并点击
        before = page.eval_on_selector_all(
            ".el-sub-menu.is-opened", "els => els.length"
        )
        page.eval_on_selector_all(
            ".el-sub-menu__title",
            """els => {
              const t = els.find(e => e.textContent.includes('运维监控'));
              if (t) t.querySelector('.el-sub-menu__icon-arrow')?.click();
            }""",
        )
        page.wait_for_timeout(800)
        after = page.eval_on_selector_all(".el-sub-menu.is-opened", "els => els.length")
        check("分组可独立收起", after < before, f"展开 {before} → {after}")

        # 重新展开
        page.eval_on_selector_all(
            ".el-sub-menu__title",
            """els => {
              const t = els.find(e => e.textContent.includes('运维监控'));
              if (t) t.querySelector('.el-sub-menu__icon-arrow')?.click();
            }""",
        )
        page.wait_for_timeout(800)
        reopened = page.eval_on_selector_all(".el-sub-menu.is-opened", "els => els.length")
        check("分组可重新展开", reopened > after, f"{after} → {reopened}")

        print("\n[3] 全部展开 / 全部收起")
        toggle = page.query_selector(".menu-actions button")
        check("存在全部展开/收起按钮", toggle is not None)
        if toggle:
            opened_before = page.eval_on_selector_all(
                ".el-sub-menu.is-opened", "els => els.length"
            )
            toggle.click()
            page.wait_for_timeout(1000)
            opened_after = page.eval_on_selector_all(
                ".el-sub-menu.is-opened", "els => els.length"
            )
            check("全部收起生效", opened_after < opened_before, f"展开 {opened_before} → {opened_after}")
            # 收起后子项在 DOM 中仍存在（只是被 CSS 隐藏），展开即可恢复
            total_items = page.eval_on_selector_all(".el-menu-item", "els => els.length")
            check("收起后菜单项仍保留在 DOM", total_items == EXPECTED_ITEM_COUNT, f"{total_items} 项")
            # 再点恢复
            page.click(".menu-actions button")
            page.wait_for_timeout(1000)
            restored = page.eval_on_selector_all(
                ".el-sub-menu.is-opened", "els => els.length"
            )
            check("全部展开恢复", restored > opened_after, f"{opened_after} → {restored}")

        print("\n[4] 搜索过滤")
        page.fill(".menu-search input", "告警")
        page.wait_for_timeout(700)
        found = page.eval_on_selector_all(
            ".el-menu-item", "els => els.map(e => e.textContent.trim())"
        )
        check("搜索命中唯一项", len(found) == 1 and "告警" in found[0], str(found))
        page.fill(".menu-search input", "")
        page.wait_for_timeout(700)
        restored_items = page.eval_on_selector_all(".el-menu-item", "els => els.length")
        check("清空后恢复全部", restored_items == EXPECTED_ITEM_COUNT, f"{restored_items} 项")

        print("\n[5] 侧边栏折叠态")
        page.click(".collapse-btn")
        page.wait_for_timeout(800)
        g_collapsed = page.eval_on_selector_all(".el-sub-menu__title", "els => els.length")
        i_collapsed = page.eval_on_selector_all(".el-menu-item", "els => els.length")
        check("折叠隐藏二级分组标题", g_collapsed == 0, f"{g_collapsed} 个")
        check("折叠菜单项完整", i_collapsed == EXPECTED_ITEM_COUNT, f"{i_collapsed} 项")
        page.click(".collapse-btn")
        page.wait_for_timeout(700)

        print("\n[6] 滚动可达性")
        # 先确保所有分组展开，菜单内容最长时才需要滚动
        page.eval_on_selector_all(
            ".el-sub-menu__title",
            """els => {
              els.forEach(t => {
                const sub = t.closest('.el-sub-menu');
                if (sub && !sub.classList.contains('is-opened')) {
                  t.querySelector('.el-sub-menu__icon-arrow')?.click();
                }
              });
            }""",
        )
        page.wait_for_timeout(900)
        scroll = page.evaluate(
            """() => {
              const s = document.querySelector('.menu-scroll');
              const wrap = s?.querySelector('.el-scrollbar__wrap');
              return {client: wrap?.clientHeight, scroll: wrap?.scrollHeight};
            }"""
        )
        check(
            "菜单区高度与内容匹配（可完整浏览）",
            bool(scroll.get("scroll")) and scroll["scroll"] >= scroll.get("client", 0),
            f"{scroll}",
        )
        page.eval_on_selector(".menu-scroll .el-scrollbar__wrap", "el => el.scrollTop = el.scrollHeight")
        page.wait_for_timeout(600)
        visible = page.eval_on_selector_all(
            ".el-menu-item", "els => els.filter(e => e.offsetParent !== null).length"
        )
        check("底部菜单项可访问", visible >= 16, f"{visible} 项可见")

        print("\n[7] 菜单跳转")
        # 确保目标分组已展开，否则菜单项不可见
        page.eval_on_selector_all(
            ".el-sub-menu__title",
            """els => {
              const t = els.find(e => e.textContent.includes('运维监控'));
              if (t && !t.closest('.el-sub-menu').classList.contains('is-opened')) {
                t.querySelector('.el-sub-menu__icon-arrow')?.click();
              }
            }""",
        )
        page.wait_for_timeout(800)
        page.eval_on_selector(".menu-scroll .el-scrollbar__wrap", "el => el.scrollTop = 0")
        page.wait_for_timeout(400)
        page.click("text=配置审批")
        page.wait_for_timeout(1800)
        check("跳转配置审批", "approval" in page.url, page.url)
        check("面包屑同步", "配置审批" in page.inner_text(".el-breadcrumb"))

        print("\n[8] 无 JS 异常")
        check("无控制台错误", len(errors) == 0, str(errors[:2]))

        browser.close()

    passed = sum(1 for _, c, _ in results if c)
    print(f"\n=== 结果：{passed}/{len(results)} 通过 ===\n")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
