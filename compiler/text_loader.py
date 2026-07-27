"""文本加载与切分 (确定性，无 LLM)。

对应 plan 第十二步流水线的前 4 步：
    TXT 导入 → 编码探测 → 文本清洗 → 章节切分 → 场景切分

这一层是纯文本处理，没有任何 AI 参与。它是 LLM 抽取的"原料供给"：
每一段喂给 LLM 的文本都带稳定的段落 ID，便于抽取结果回指原文。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional


# ---------------------------------------------------------------------------
# 编码探测 & 加载
# ---------------------------------------------------------------------------

_CANDIDATE_ENCODINGS = ("utf-8", "utf-8-sig", "gbk", "gb18030", "big5")


def load_novel(path: str, encoding: Optional[str] = None) -> str:
    """读取小说 TXT，自动探测编码。

    encoding 显式指定时直接用；否则依次尝试常见中文编码。
    """
    if encoding is not None:
        with open(path, "r", encoding=encoding) as f:
            return f.read()
    last_err: Optional[Exception] = None
    for enc in _CANDIDATE_ENCODINGS:
        try:
            with open(path, "r", encoding=enc) as f:
                text = f.read()
            # 合理性检查：中文小说若解码成大量替换符，多半是错码
            if text.count("\ufffd") > len(text) * 0.01:
                continue
            return text
        except (UnicodeDecodeError, LookupError) as e:
            last_err = e
            continue
    raise UnicodeDecodeError(
        "unknown", b"", 0, 1,
        f"无法用 { _CANDIDATE_ENCODINGS } 解码 {path}: {last_err}",
    )


# ---------------------------------------------------------------------------
# 文本清洗
# ---------------------------------------------------------------------------

# 全角空格段落前缀 (中文小说排版惯例)
_PARA_INDENT = "\u3000\u3000"
# 章节标题正则：
# - 第N章 标题
# - 站点导出常见的 "1.第1章标题" / "1、第1章标题"
_CHAPTER_RE = re.compile(
    r"^(?:\d+[.、][ 　]*)?"
    r"第([\d零一二三四五六七八九十百千两]+)章[ 　]*(.*)$"
)


def clean_text(text: str) -> str:
    """清洗：统一换行、去广告行、去多余空行。保留段落缩进结构。"""
    # 统一换行符
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    out: List[str] = []
    for ln in lines:
        stripped = ln.strip()
        # 跳过纯空行 (后面会按段落重组)
        if not stripped:
            out.append("")
            continue
        # 跳过明显的广告/水印行 (启发式)
        if _is_ad_line(stripped):
            continue
        # 去掉行尾多余空白但保留前导全角缩进
        if ln.startswith(_PARA_INDENT):
            out.append(_PARA_INDENT + stripped)
        else:
            out.append(stripped)
    # 合并连续空行为单个
    cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(out))
    return cleaned.strip() + "\n"


_AD_HINTS = (
    "本书来自", "更多精彩", "请搜索", "扫码", "二维码", "www.", "http",
    "百度搜", "笔趣阁", "免费阅读", "下载APP",
)


def _is_ad_line(line: str) -> bool:
    if len(line) > 80:
        # 长行通常是正文，但若是纯 url/广告也剔除
        low = line.lower()
        if any(h in low for h in ("http", "www.", ".com")):
            return True
        return False
    return any(h in line for h in _AD_HINTS)


# ---------------------------------------------------------------------------
# 章节切分
# ---------------------------------------------------------------------------


@dataclass
class Chapter:
    """一个章节。content 是清洗后的正文 (含段落缩进)。"""

    index: int  # 从 1 开始
    raw_number: str  # 原文章节号字符串，如 "1" / "一"
    title: str
    content: str
    # 该章在原文中的字符偏移 (便于回指)
    start_offset: int = 0
    paragraphs: List[str] = field(default_factory=list)

    @property
    def chapter_id(self) -> str:
        return f"ch_{self.index:04d}"

    @property
    def heading(self) -> str:
        return f"第{self.raw_number}章 {self.title}".strip()


def split_chapters(text: str, *, skip_header: bool = True) -> List[Chapter]:
    """把整本小说切成章节列表。

    识别行首的 "第N章 标题"。N 支持阿拉伯数字与中文数字。
    章节标题行之前的内容视为前言/简介 (默认跳过)。
    """
    lines = text.split("\n")
    line_offsets: List[int] = []
    current_offset = 0
    for line in lines:
        line_offsets.append(current_offset)
        current_offset += len(line) + 1
    # 找出所有章节标题行 (行号, raw_number, title)
    marks: List[tuple] = []
    for i, ln in enumerate(lines):
        m = _CHAPTER_RE.match(ln.strip())
        if m:
            marks.append((i, m.group(1), m.group(2).strip()))

    chapters: List[Chapter] = []
    for idx, (lineno, num, title) in enumerate(marks):
        start = lineno + 1
        end = marks[idx + 1][0] if idx + 1 < len(marks) else len(lines)
        body = "\n".join(lines[start:end])
        body = clean_text(body)
        paras = _paragraphs(body)
        chapters.append(Chapter(
            index=idx + 1,
            raw_number=num,
            title=title,
            content=body,
            start_offset=line_offsets[lineno],
            paragraphs=paras,
        ))

    if skip_header and chapters:
        return chapters
    # 不跳过 header：把第一个章节前的内容当成 "第0章 前言"
    if not skip_header and marks and marks[0][0] > 0:
        head_body = clean_text("\n".join(lines[: marks[0][0]]))
        if head_body.strip():
            chapters.insert(0, Chapter(
                index=0, raw_number="0", title="前言",
                content=head_body, paragraphs=_paragraphs(head_body),
            ))
    return chapters


def _paragraphs(body: str) -> List[str]:
    """把章节正文切成段落。以全角缩进或空行分隔。"""
    paras: List[str] = []
    for chunk in re.split(r"\n{1,}", body):
        s = chunk.strip()
        if not s:
            continue
        # 去掉前导全角空格，保留正文
        s = s.lstrip("\u3000").strip()
        if s:
            paras.append(s)
    return paras


# ---------------------------------------------------------------------------
# 场景切分 (启发式)
# ---------------------------------------------------------------------------

# 场景切换的软信号：时间词、地点词、明显视角切换
_SCENE_TIME_HINTS = (
    "翌日", "次日", "数日后", "几日后", "半月后", "一个月后", "半年后",
    "入夜", "入夜后", "深夜", "清晨", "黄昏", "傍晚", "午时", "亥时",
    "此时", "另一边", "与此同时", "却说", "话说",
)
_SCENE_LOC_HINTS = (
    "回到", "来到", "走进", "走进去", "出了", "踏进", "抵达",
)
# 单场景目标字数区间 (太小没信息量，太大超出 LLM 上下文)
_SCENE_MIN_CHARS = 400
_SCENE_MAX_CHARS = 2500


@dataclass
class Scene:
    """章节内的一个场景片段。"""

    chapter_index: int
    scene_index: int  # 章内序号，从 1 开始
    text: str
    paragraph_range: tuple  # (start, end) 在 chapter.paragraphs 上的索引

    @property
    def scene_id(self) -> str:
        return f"ch{self.chapter_index:04d}_sc{self.scene_index:02d}"


def split_scenes(chapter: Chapter, *, max_chars: int = _SCENE_MAX_CHARS) -> List[Scene]:
    """把一章切成若干场景。

    启发式策略 (优先级从高到低)：
      1. 段落首句命中时间/地点切换词 -> 新场景边界
      2. 累积字数超过 max_chars -> 强制切 (防爆上下文)
      3. 空行间隔 -> 弱边界，仅当当前场景已够长才切

    每个场景至少 _SCENE_MIN_CHARS 字 (太短就并入下一场景)。
    """
    paras = chapter.paragraphs
    if not paras:
        return []

    scenes: List[Scene] = []
    cur_paras: List[str] = []
    cur_start = 0

    def flush(end: int):
        if not cur_paras:
            return
        text = "\n".join(cur_paras)
        # 太短不单独成景：并到上一个场景
        if scenes and len(text) < _SCENE_MIN_CHARS:
            prev = scenes[-1]
            prev.text = prev.text + "\n" + text
            prev.paragraph_range = (prev.paragraph_range[0], end)
        else:
            scenes.append(Scene(
                chapter_index=chapter.index,
                scene_index=len(scenes) + 1,
                text=text,
                paragraph_range=(cur_start, end),
            ))

    for i, p in enumerate(paras):
        is_boundary = _looks_like_scene_boundary(p) and cur_paras
        will_too_long = cur_paras and (sum(len(x) for x in cur_paras) + len(p) > max_chars)
        if is_boundary or will_too_long:
            flush(i)
            cur_start = i
            cur_paras = [p]
        else:
            if not cur_paras:
                cur_start = i
            cur_paras.append(p)
    flush(len(paras))

    # 重排 scene_index (合并后可能不连续)
    for j, sc in enumerate(scenes):
        sc.scene_index = j + 1
    return scenes


def _looks_like_scene_boundary(paragraph: str) -> bool:
    """段落开头是否像场景切换。"""
    head = paragraph[:12]
    if any(h in head for h in _SCENE_TIME_HINTS):
        return True
    if any(h in head for h in _SCENE_LOC_HINTS):
        return True
    return False
