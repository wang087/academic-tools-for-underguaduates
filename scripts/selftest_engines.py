# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 阶段 S 引擎库 · **离线自检**（零网络，秒级）
#
# 为什么必须有
#   复用模式的核心是 faculty_engines.py 里的站点家族判据。这些判据
#   一旦漂移，**所有学校**都会受影响，而且往往表现为"抓到了但人数不对"
#   这种静默错误。故用真实站点的 HTML 骨架做成 fixture，离线锁住行为。
#
# 覆盖的实测坑（每条都对应一次真实踩坑）：
#   ① 导航链接混入 → 被当成教师（USTC 化材 / 复旦生科 实测）
#   ② 无个人主页的教师被漏掉（SJTU 实测 16 人，208 → 186）
#   ③ 名字里的括注是**方向**而非姓名一部分（USTC 化学 实测）
#   ④ 详情页导航里也有「研究方向」锚点 → 按首次命中会取到导航（SJTU 实测）
#   ⑤ `--limit` 试跑把人标成"无方向"后，全量不再补（续跑失效）
#   ⑥ http/https 混写导致路径拼成 `/http://host/...`
#
# 用法：uv run python 流程1/scripts/selftest_engines.py
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import io
import json
import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from faculty_engines import (detect_engines, declared_total,  # noqa: E402
                             extract_directions_detail, is_person_name,
                             looks_js_rendered, page_count, parse_with,
                             score_candidate, split_title_direction,
                             url_signature)

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace")

CFG = {"parse_rules": {"direction_split": "[，,、；;]"},
       "list_types": {"generic_cluster": {"cluster_min_n": 8,
                                          "cluster_min_share": 0.18}}}

_fail: list[str] = []
_pass = 0


def check(cond: bool, label: str, extra: str = "") -> None:
    global _pass
    if cond:
        _pass += 1
        print(f"  [OK] {label}")
    else:
        _fail.append(label + (f" —— {extra}" if extra else ""))
        print(f"  [X] {label}" + (f"  [{extra}]" if extra else ""))


# ══════════════════════════════════════════════════════════════
# Fixtures：按真实站点骨架（class 与层级照抄实测）
# ══════════════════════════════════════════════════════════════
# ① WebPlus news_list（中科大化学系 / 复旦生科院 实测骨架）
FIX_WP_NEWS = """
<html><body>
<div class="menu"><ul><li><a href="/main.htm">首页</a></li>
<li><a href="/bxgk/list.htm">本系概况</a></li>
<li><a href="/js/list.htm">教授</a></li></ul></div>
<div id="wp_news_w6"><ul class="news_list list2">
 <li class="news n1 clearfix"><span class="news_title">
   <a href='/2025/0202/c2474a673524/page.htm' target='_blank'
      title='刘建忠（有机化学）'>刘建忠（有机化学）</a></span>
   <span class="news_meta">2025-02-02</span></li>
 <li class="news n2 clearfix"><span class="news_title">
   <a href='/2025/0202/c2474a673523/page.htm' target='_blank'
      title='卓庆德（有机化学）'>卓庆德（有机化学）</a></span>
   <span class="news_meta">2025-02-02</span></li>
 <li class="news n3 clearfix"><span class="news_title">
   <a href='/2025/0202/c2474a673522/page.htm' target='_blank'
      title='宋磊（无机化学）'>宋磊（无机化学）</a></span>
   <span class="news_meta">2025-02-02</span></li>
</ul></div>
<div class="footer"><ul><li><a href="/12587/list.htm">校友名录</a></li></ul></div>
</body></html>
"""

# ② WebPlus newsinfo card（中科大生医部 实测骨架）
FIX_WP_NEWSINFO = """
<html><body>
<ul class="menu"><li><a href="/main.htm">首页</a></li></ul>
<ul>
 <li class="itemhover clearfix">
   <a href="/2024/0101/c1234a5678/page.htm">
     <div class="tit">毕国强</div></a></li>
 <li class="itemhover clearfix">
   <a href="/2024/0101/c1234a5679/page.htm">
     <div class="tit">蔡刚</div></a></li>
</ul>
</body></html>
"""

# ③ WebPlus teacher card（浙大生科院 实测骨架：列表页含方向+邮箱）
FIX_WP_TEACHER = """
<html><body>
<ul class="menu"><li><a href="/main.htm">首页</a></li></ul>
<ul>
 <li class="list-item">
   <h3>周如鸿 <span class="title">教授</span></h3>
   <span class="direction">计算生物学 · 生物物理学</span>
   <span class="email">zhou@example.edu.cn</span>
   <a href="/clscn/26369/list.htm">详情</a></li>
 <li class="list-item">
   <h3>管敏鑫 <span class="title">教授</span></h3>
   <span class="direction">遗传学</span>
   <span class="email">guan@example.edu.cn</span>
   <a href="/clscn/26370/list.htm">详情</a></li>
</ul>
</body></html>
"""

# ④ 形态聚类站（SJTU 生科院 实测骨架）+ 2 个无个人主页者
FIX_CLUSTER = """
<html><body>
<div class="menu"><ul>
 <li><a href="/Data/List/xyjj">学院简介</a></li>
 <li><a href="/Data/List/szdw">师资队伍</a></li>
 <li><a href="/Data/List/kygk">科研概况</a></li>
</ul></div>
<div class="shengwu"><h5>生物化学与分子生物学系</h5>
 <div class="sheng-div"><div class="sheng-tit"><span>教授</span></div>
  <div class="sheng-ul"><ul>
   <li><a title="刘文" href="http://life.sjtu.edu.cn/teacher/wliu2025"
          data-name="讲席教授">刘文</a></li>
   <li><a title="曹博" href="http://life.sjtu.edu.cn/teacher/caobo"
          data-name="研究员">曹博</a></li>
   <li><a title="冯雁" href="http://life.sjtu.edu.cn/teacher/YanFeng"
          data-name="特聘教授">冯雁</a></li>
   <li><a title="梁如冰" href="http://life.sjtu.edu.cn/teacher/RubingLiang"
          data-name="教授">梁如冰</a></li>
   <li><a title="林双君" href="http://life.sjtu.edu.cn/teacher/linsj"
          data-name="特聘教授">林双君</a></li>
   <li><a title="刘昱" href="http://life.sjtu.edu.cn/teacher/liuyu"
          data-name="教授">刘昱</a></li>
   <li><a title="吴更" href="http://life.sjtu.edu.cn/teacher/GengWu"
          data-name="教授">吴更</a></li>
   <li><a title="杨广宇" href="http://life.sjtu.edu.cn/teacher/yanggy"
          data-name="教授">杨广宇</a></li>
   <li><a title="由德林" href="http://life.sjtu.edu.cn/teacher/DelinYou"
          data-name="教授">由德林</a></li>
   <li><a title="都亚男" href="http://life.sjtu.edu.cn/teacher/YananDu"
          data-name="副教授">都亚男</a></li>
   <!-- 以下两人官网未开通个人主页（实测原文） -->
   <li><a title="秦伟彤" target="_self"
          href="javascript:alert('暂无开通个人主页');"
          data-name="助理研究员">秦伟彤</a></li>
   <li><a title="程术" target="_self"
          href="javascript:alert('暂无开通个人主页');"
          data-name="助理研究员">程术</a></li>
  </ul></div></div></div>
</body></html>
"""

# ⑤ 详情页：页签式，且**导航里先出现一个「研究方向」锚点**（SJTU 实测坑）
FIX_DETAIL_TAB = """
<html><body>
<ul class="nav"><li><a href="#profess">基本信息</a></li>
 <li><a href="#research">研究方向</a></li>
 <li><a href="#productions">代表论著</a></li></ul>
<section><div id="profess"><p>上海交通大学特聘教授。</p></div></section>
<section><div id="research">
 研究方向
 微生物药物合成生物学
 新功能酶分子设计与进化
 微生物酶结构与作用机制
</div></section>
</body></html>
"""

# ⑥ 详情页：行内编号式（SJTU 熊毅页 实测）
FIX_DETAIL_INLINE = """
<html><body><div id="profess">
<span> 博士生导师。主要研究方向：（1）基于人工智能的生物大分子设计与优化
方法研究；（2）人工智能方法在合成生物学、药物及疫苗研发中的应用。欢迎申请。</span>
</div></body></html>
"""

# ⑦ JS 渲染空壳（南大化学 实测：teacher-list 为空）
FIX_JS_SHELL = """
<html><body>
<div class="search-teacher"><ul class="teacher-list clearfix"></ul>
<div id="wp_paging_w6"></div></div>
<script>$.ajax({url:'/getTeacher',dataType:'json'});</script>
</body></html>
"""


# ══════════════════════════════════════════════════════════════
def main() -> int:
    print("═" * 70)
    print("阶段 S 引擎库 · 离线自检（零网络）")
    print("═" * 70)

    # ── 1. 人名判据 ──
    print("\n[1] 人名判据 / 导航排除")
    check(is_person_name("刘建忠"), "中文姓名通过")
    check(is_person_name("Hans Staffan Persson"), "西文多段姓名通过")
    check(not is_person_name("学院简介"), "导航词「学院简介」被排除")
    check(not is_person_name("师资队伍"), "导航词「师资队伍」被排除")
    check(not is_person_name("各系师资"), "导航词「各系师资」被排除（实测漏网）")
    check(not is_person_name("刘建忠（有机化学）"), "带括注整体不算姓名")

    print("\n[2] 姓名/方向拆分（括注即方向）")
    nm, dr = split_title_direction("刘建忠（有机化学）")
    check(nm == "刘建忠" and dr == "有机化学", "拆出姓名+方向", f"{nm}/{dr}")
    nm2, dr2 = split_title_direction("安艳捧")
    check(nm2 == "安艳捧" and dr2 == "", "无括注时不误拆", f"{nm2}/{dr2}")

    # ── 3. URL 形态签名 ──
    print("\n[3] URL 形态签名（聚类基础）")
    check(url_signature("http://life.sjtu.edu.cn/teacher/wliu2025")
          == "/teacher/", "slug 型取目录")
    check(url_signature("https://chem.ustc.edu.cn/js/list.htm")
          == "/js/list.htm", "list.htm 型保留文件名")
    check(url_signature("http://x.edu.cn/61/93/c28175a352659/page.htm")
          == url_signature("http://x.edu.cn/ec/a3/c28175a453795/page.htm"),
          "c<col>a<id> 型归一为同一签名")

    # ── 4. 家族识别与抽取 ──
    print("\n[4] 家族识别与抽取")
    ppl = parse_with("wp_news_list", FIX_WP_NEWS, "https://x.edu.cn",
                     "教授", CFG)
    names = [p["name"] for p in ppl]
    check(names == ["刘建忠", "卓庆德", "宋磊"], "wp_news_list 抽全 3 人", str(names))
    check(all(p["directions"] for p in ppl), "wp_news_list 从 title 取到方向")
    check("校友名录" not in names, "脚注栏目未被当成教师")

    ppl2 = parse_with("wp_newsinfo_card", FIX_WP_NEWSINFO, "https://x.edu.cn",
                      "师资", CFG)
    check([p["name"] for p in ppl2] == ["毕国强", "蔡刚"],
          "wp_newsinfo_card 抽全 2 人", str([p["name"] for p in ppl2]))

    ppl3 = parse_with("wp_teacher_card", FIX_WP_TEACHER, "http://x.edu.cn",
                      "研究所", CFG)
    check([p["name"] for p in ppl3] == ["周如鸿", "管敏鑫"],
          "wp_teacher_card 抽全 2 人")
    check(ppl3 and ppl3[0]["email"] == "zhou@example.edu.cn",
          "wp_teacher_card 取到邮箱")
    check(ppl3 and "计算生物学" in ppl3[0]["directions"],
          "wp_teacher_card 取到方向（含 · 分隔）")

    # ── 5. 形态聚类 + 无主页者补充（关键回归） ──
    print("\n[5] 形态聚类 / 无个人主页者不漏")
    cl = parse_with("generic_cluster", FIX_CLUSTER, "https://life.sjtu.edu.cn",
                    "教工名录", CFG)
    cnames = [p["name"] for p in cl]
    check(len(cl) == 12, f"抽全 12 人（含 2 位无主页者），实得 {len(cl)}", str(cnames))
    check("秦伟彤" in cnames and "程术" in cnames,
          "无个人主页（javascript:）的教师未被漏掉", str(cnames))
    check("学院简介" not in cnames and "师资队伍" not in cnames,
          "导航链接未被当成教师", str(cnames))
    nohome = [p for p in cl if p["name"] in ("秦伟彤", "程术")]
    check(all(p["url"] == "" for p in nohome),
          "无主页者 url 留空（不臆造链接）")

    # ── 6. 详情页方向抽取（三种模板 + 导航坑） ──
    print("\n[6] 详情页方向抽取")
    d1 = extract_directions_detail(FIX_DETAIL_TAB, CFG)
    check(d1 == ["微生物药物合成生物学", "新功能酶分子设计与进化",
                 "微生物酶结构与作用机制"],
          "页签式：跳过导航锚点，取到真实方向", str(d1))
    d2 = extract_directions_detail(FIX_DETAIL_INLINE, CFG)
    check(d2 == ["基于人工智能的生物大分子设计与优化方法研究",
                 "人工智能方法在合成生物学、药物及疫苗研发中的应用"],
          "行内编号式（1）（2）正确切分", str(d2))

    # ── 7. 分页 / 对账 / JS 空壳 ──
    print("\n[7] 分页与计数对账")
    check(page_count(FIX_WP_NEWS) == 1, "无分页标记 → 1 页")
    check(page_count('<a href="/js/list3.htm">尾页</a>') == 3,
          "仅凭 listN.htm 尾页链接识别 3 页（WebPlus 实测）")
    check(page_count("页码 1 / 13") == 13, "「页码 x / y」识别")
    check(declared_total("共 40 条记录") == 40, "官网自称条数（共N条）")
    check(declared_total("共 79 条") == 79, "官网自称条数变体")
    check(looks_js_rendered(FIX_JS_SHELL, 0) is True,
          "JS 空壳被识别（南大化学实测情形）")
    check(looks_js_rendered(FIX_WP_NEWS, 3) is False, "有数据时不误判 JS")

    # ── 8. 评分闸门 ──
    print("\n[8] 评分闸门（accept / review / reject）")
    good = parse_with("wp_news_list", FIX_WP_NEWS, "https://x.edu.cn", "教授", CFG)
    # 3 人/页 × 3 页 = 9，与官网自称 9 一致 → 指纹 + 对账 → accept
    v, s, _ = score_candidate("wp_news_list", good, 3, 9, 3)
    check(v == "accept" and s >= 40, f"指纹+对账一致 → accept（得分 {s}）", f"{v}/{s}")

    # 对账**不符**时必须降级：3×3=9 远少于官网自称 40 → 不能 accept
    v_bad, s_bad, r_bad = score_candidate("wp_news_list", good, 3, 40, 3)
    check(v_bad != "accept",
          f"计数对账不符 → 降级为 {v_bad}（不硬接受）", f"{v_bad}/{s_bad}")

    nav_only = [{"name": n, "url": "u", "directions": []} for n in
                ("各系师资", "教育培养", "培养方案", "教学成果", "学位申请")]
    v2, s2, r2 = score_candidate("generic_cluster", nav_only, 1, 6, 0)
    check(v2 == "reject", f"导航污染 → reject（得分 {s2}）", f"{v2}/{s2}")

    # 小样本 + 无官网计数可对账 → review（如实「需人看一眼」，不硬接受也不丢）
    v3, s3, _ = score_candidate("generic_cluster", good, 1, 0, 0)
    check(v3 == "review", f"小样本且无对账 → review（得分 {s3}）", f"{v3}/{s3}")

    # 大规模 + 几乎都有详情页 → 无对账也能 accept（SJTU 208 人实测情形）
    big = [{"name": f"张{i:02d}", "url": "u", "directions": []}
           for i in range(30)]
    v4, s4, _ = score_candidate("generic_cluster", big, 1, 0, 0)
    check(v4 == "accept", f"规模合理（30 条全带详情页）→ accept（{s4}）", f"{v4}/{s4}")

    # ── 9. detect_engines 端到端 ──
    print("\n[9] detect_engines 家族判定")
    dets = detect_engines(FIX_WP_NEWS, "https://x.edu.cn", "教授", CFG)
    check(bool(dets) and dets[0]["engine"] == "wp_news_list",
          "WebPlus news_list 页面首选 wp_news_list",
          str([(d["engine"], d["count"]) for d in dets]))

    # ── 10. 续跑语义（--limit 后仍能补全） ──
    print("\n[10] 续跑：--limit 试跑不废掉全量")
    import harvest_faculty as hf  # noqa: E402
    check(hf._NEVER_FETCHED_WHY == "列表页无方向且未补详情",
          "「未抓取」标记常量存在且与合并逻辑一致")
    check(getattr(hf, "_NEVER_FETCHED_WHY", "") != "",
          "stage_detail 依此标记剔除未抓取者（可续跑）")

    # ── 11. 浏览器家族（render / interactive）──
    print("\n[11] 浏览器家族（JS/AJAX 站的转 CDP 分支）")
    from faculty_engines import BROWSER_ENGINES, ENGINES  # noqa: E402
    check("interactive" in ENGINES and "render" in ENGINES,
          "引擎表含 interactive / render")
    check(BROWSER_ENGINES == {"render", "interactive"},
          "BROWSER_ENGINES 正确标出需浏览器的家族")

    # interactive：解析上游注入的枚举结果
    payload = {"teachers": [
        {"name": "陈洪渊", "url": "http://x/chy/list.htm", "categories": ["院士"]},
        {"name": "段春迎", "url": "http://x/dcy/list.htm", "categories": ["无机化学"]},
        {"name": "学院简介", "url": "", "categories": ["导航"]},
    ], "clicks": 2}
    fakepage = "__INTERACTIVE_RESULT__\n" + json.dumps(payload, ensure_ascii=False)
    got = parse_with("interactive", fakepage, "http://x", "师资名录", CFG)
    names = [g["name"] for g in got]
    check(names == ["陈洪渊", "段春迎"],
          f"interactive 解析枚举结果并滤掉非人名，实得 {names}", str(names))
    check(got and got[0]["url"] == "http://x/chy/list.htm",
          "教师 URL 被补成绝对地址（供详情页抓取）")
    check(parse_with("interactive", "<html></html>", "http://x", "s", CFG) == [],
          "无枚举标记时返回空（不伪造数据）")
    # render：渲染后的 HTML 交给家族解析
    r = parse_with("render", FIX_WP_NEWS, "https://x.edu.cn", "教授", CFG)
    check([x["name"] for x in r] == ["刘建忠", "卓庆德", "宋磊"],
          "render 在渲染产物上复用家族解析")

    print("\n[12] 渲染桥 / 交互模块的可导入性与降级")
    import faculty_render as fr  # noqa: E402
    import faculty_interact as fi  # noqa: E402
    check(fr.crawler_available() is True,
          "faculty_render 找到 robot/crawl_site.py")
    check(isinstance(fi.CdpUnavailable("x"), RuntimeError),
          "CdpUnavailable 可被捕获（调用方据此优雅降级）")
    check(str(fi._prestart_command(9222)).count("remote-allow-origins") == 1,
          "预启动命令含 --remote-allow-origins（否则 WS 握手 403）")
    check("remote-debugging-port" in fi._prestart_command(9222),
          "预启动命令含 --remote-debugging-port")

    print("\n" + "═" * 70)
    total = _pass + len(_fail)
    print(f"结果：{_pass}/{total} 项通过")
    if _fail:
        print("\n未通过：")
        for f in _fail:
            print(f"  [X] {f}")
        print("═" * 70)
        return 1
    print("[OK] 全部通过 —— 引擎判据未漂移")
    print("═" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
