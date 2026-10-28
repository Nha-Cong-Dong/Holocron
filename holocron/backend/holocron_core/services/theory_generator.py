"""Bộ sinh câu lý thuyết MCQ4 tất định từ cặp khái niệm đã xác nhận (ADR-007; SPECIFICATION §0 I2–I4, §5.8, §5.11).

Thuộc Deterministic Core: hàm thuần, không gọi LLM, không gọi mạng, không rẽ nhánh theo môn hay chương.

- **Nhóm ứng viên:** chỉ cặp `confirmed` cùng `subject_code` với slot (ADR-007 mục 1). Cặp chưa xác nhận, bị từ chối
  hay thuộc môn khác không bao giờ được dùng và không tính vào `pool_sha256`.
- **Ba dạng câu (ADR-007 mục 1):** ghép khái niệm, điền khuyết, chọn định nghĩa đúng. Thân câu là khung cố định do
  code viết cộng trích dẫn nguyên văn; mọi phương án là `term` hoặc `definition_quote` của một cặp (tên khái niệm bên
  trong định nghĩa được che bằng `____`). Không có văn bản nào do LLM soạn.
- **Phương án nhiễu (I2):** ưu tiên cặp cùng chương rồi mới tới cùng môn; mỗi phương án nhiễu mang đúng một mã thuộc
  `THEORY_MISCONCEPTIONS` theo nguồn chọn. Dạng chọn định nghĩa đúng chỉ lấy khái niệm cùng chương.
- **Trích dẫn (I3):** `citation` của câu và trích dẫn riêng của từng phương án nhiễu có `verbatim_quote` là
  `definition_quote` của cặp tương ứng; trích dẫn được kiểm lại là chuỗi con (NFC + gộp khoảng trắng) của
  `raw_text` của chunk nguồn trước khi dùng.
- **Tái lập (I4):** mọi lựa chọn ngẫu nhiên chỉ dùng `random.Random(f"{generator_version}:{pool_sha256}:{seed}")`;
  ứng viên luôn được sắp theo thứ tự chuẩn trước khi xáo, nên thứ tự cặp ở đầu vào không ảnh hưởng kết quả.
- **Dạng câu do RNG chọn (I4; duyệt 2026-10-26, spec §5.11, ADR-007 mục 2):** dạng câu không phải đầu vào; chính RNG
  của bộ ba I4 rút dạng câu trong các dạng khả thi của nhóm ứng viên, nên `(generator_version, pool_sha256, seed)` cùng
  chương của slot đủ để tái lập câu. Các chi tiết sau là **đề xuất, chờ duyệt (spec §5.11, §17 D8)**; code làm đúng
  theo đề xuất đó: (a) thứ tự rút là dạng trước, cặp sau (rồi thứ tự phương án nhiễu, rồi thứ tự bốn phương án);
  (b) danh sách dạng khả thi giữ thứ tự khai báo cố định `FORM_ORDER` (ghép khái niệm, điền khuyết, chọn định nghĩa
  đúng), và một dạng là khả thi khi có ít nhất một bản nháp của dạng đó (cặp đáp án thuộc chương của slot nếu slot giới
  hạn chương, cộng thuật ngữ bị che với dạng điền khuyết) mà luật lấy phương án nhiễu ở mục "Không đủ căn cứ" cho đủ 3
  phương án nhiễu hợp lệ; tính khả thi được xác định trước khi rút số ngẫu nhiên nào; (c) không dạng nào khả thi thì
  trả `ReviewTicket` với lý do "không đủ căn cứ trong Vault".
- **Đúng một đáp án:** mọi phép so đoạn văn của luật này (trích dẫn với trích dẫn, đoạn đã điền với trích dẫn) làm
  trên dạng đã chuẩn hoá: NFC, gộp khoảng trắng, không phân biệt hoa thường, bỏ khoảng trắng và dấu câu ở hai đầu mỗi
  đoạn (dấu câu ở giữa giữ nguyên), để dấu chấm cuối câu hay ngoặc bao ngoài không làm trượt phép so chuỗi con. Cặp có
  `definition_quote` trùng, chứa hoặc nằm trong trích dẫn của cặp đáp án cũng là một đáp án đúng. Dạng chọn định nghĩa
  (phương án là định nghĩa của từng cặp) bỏ đúng cặp đó, và bỏ cả cặp mà khi điền tên khái niệm được hỏi vào mọi chỗ
  `____` của phương án thì ra một đoạn khớp một trích dẫn của nhóm ứng viên theo hai chiều của luật điền thử dưới đây
  (ví dụ hỏi "Không gian" thì "____ là hình thức tồn tại của vật chất" của "Thời gian" điền ra một đoạn nằm trong trích
  dẫn của chính "Không gian"; chỗ che vốn là tên khái niệm được hỏi thì điền lại không đổi văn bản, không tính là căn
  cứ). Hai dạng có phương án là thuật ngữ (ghép khái niệm, điền khuyết) xét theo **khái niệm** (khoá
  `term` sau cùng phép chuẩn hoá), vì đáp án đúng của chúng xác định theo khái niệm: khái niệm có ít nhất một cặp trong
  nhóm ứng viên xung đột với đáp án thì mọi cặp của khái niệm đó bị loại khỏi phương án nhiễu (ví dụ "Hình thức tồn
  tại" có một cặp trùng trích dẫn với "Không gian" và một cặp khác trích dẫn: hỏi "Không gian" thì không cặp nào của
  "Hình thức tồn tại" làm phương án nhiễu). Ở hai dạng này một cặp xung đột khi trích dẫn của nó lồng với trích dẫn đáp
  án như trên, hoặc khi thuật ngữ của nó điền vào chỗ `____` của thân câu cho ra một đoạn nằm trong `definition_quote`
  của một cặp thuộc nhóm ứng viên, hay chứa một `definition_quote` như vậy ở vị trí chạm vào thuật ngữ vừa điền (ví dụ
  câu ghép khái niệm hỏi "Không gian" với "____ là hình thức tồn tại của vật chất." không nhận "Thời gian" khi "Thời
  gian là hình thức tồn tại của vật chất." cũng là trích dẫn trong nhóm ứng viên, kể cả khi thân câu dài hơn trích dẫn
  đó); dạng điền khuyết còn loại thuật ngữ trùng, chứa hoặc nằm trong thuật ngữ bị che. Luật này chỉ đọc dữ liệu đã nằm
  trong `pool_sha256` (I4): `raw_text` của chunk không được dùng, nên thêm hay sửa chunk cùng môn mà không đổi cặp nào
  thì câu không đổi. Câu vẫn có thể có đáp án đúng thứ hai nằm trong một chunk chưa có cặp xác nhận; người dùng giảm rủi
  ro này bằng cách xác nhận thêm cặp cho đoạn văn đó.
- **Không đủ căn cứ:** không dạng câu nào chọn được 3 phương án nhiễu khác nhau thì trả `ReviewTicket`, không bịa
  phương án. Phương án nhiễu hợp lệ được lấy tham lam theo thứ tự chuẩn của nhóm ứng viên (cặp cùng chương trước): bỏ
  cặp trùng khái niệm hoặc trùng văn bản phương án với đáp án hay với một cặp đã nhận đứng trước, và cặp bị luật "đúng
  một đáp án" loại (ở hai dạng có phương án là thuật ngữ: mọi cặp của một khái niệm có cặp xung đột); nên tính khả thi
  không phụ thuộc RNG. Ví dụ hai cặp cùng `term` khác trích dẫn, không cặp nào xung đột với đáp án, thì chỉ biến thể
  đứng trước trong thứ tự chuẩn làm phương án nhiễu.
- **Giới hạn đã biết của luật tham lam:** một cặp đứng trước đã nhận có thể chặn hai cặp đứng sau vốn hợp lệ với nhau
  (cặp A khái niệm X văn bản t, cặp B khái niệm X văn bản u, cặp C khái niệm Y văn bản t: nhận A thì bỏ cả B lẫn C dù
  B và C cùng nhận được; thực tế gặp ở dạng chọn định nghĩa, khi hai định nghĩa trùng nhau sau khi che tên khái niệm).
  Vì vậy bộ sinh có thể báo không khả thi (`ReviewTicket`) dù tồn tại bộ 3 phương án nhiễu hợp lệ. Đổi sang luật tìm
  bộ 3 tối ưu là đổi luật chọn, phải tăng `GENERATOR_VERSION`.
- **Giới hạn đã biết:** che tên khái niệm chỉ khớp đúng chuỗi `term` (không phân biệt hoa thường, đúng ranh giới chữ);
  dạng biến hình hay số nhiều ("Process" và "Processes") không bị che và có thể lộ đáp án. Bộ sinh cố ý không tách từ
  hay biến hình theo ngôn ngữ.

`TheoryQuestion`, `TheoryQuestionForm`, `THEORY_MISCONCEPTIONS` và các luật so khớp mà validator của `TheoryQuestion`
dùng nằm ở `domain/theory.py` (để `domain/twins.py` dùng được); module này import lại và re-export mọi tên cũ.
"""

from __future__ import annotations

import hashlib
import json
import random
import unicodedata
from collections.abc import Callable, Iterator, Mapping, Sequence
from typing import NamedTuple

from ..domain.repositories import VaultRepository
from ..domain.schemas import (
    SEED_SPACE,
    Citation,
    KnowledgeChunk,
    MisconceptionRef,
    OptionKey,
    StrictModel,
)
from ..domain.theory import (
    THEORY_MISCONCEPTIONS,
    THEORY_SAME_CHAPTER_CONCEPT,
    THEORY_SAME_SUBJECT_CONCEPT,
    TheoryQuestion,
    TheoryQuestionForm,
    _is_edge_char,
    _match_key,
    _option_key,
    _overlaps,
    _quotes_overlap,
    _trim_edges,
)
from ..domain.twins import ReviewTicket
from ..domain.vault import (
    ConceptPair,
    ConceptPairStatus,
    check_definition_quote,
    find_term_spans,
)

# Tên chuyển sang `domain/theory.py` vẫn import được từ module này (kể cả hàm nội bộ mà test hay module khác dùng).
__all__ = [
    "BLANK",
    "FORM_ORDER",
    "GENERATOR_VERSION",
    "NOT_ENOUGH_EVIDENCE",
    "THEORY_MISCONCEPTIONS",
    "THEORY_SAME_CHAPTER_CONCEPT",
    "THEORY_SAME_SUBJECT_CONCEPT",
    "TheoryQuestion",
    "TheoryQuestionForm",
    "_is_edge_char",
    "_match_key",
    "_option_key",
    "_overlaps",
    "_quotes_overlap",
    "_trim_edges",
    "candidate_pool",
    "feasible_forms",
    "generate_theory_question",
    "generate_theory_question_from_vault",
    "mask_terms",
    "pool_sha256",
    "theory_rng",
]

GENERATOR_VERSION = "theory_mcq4_v2"
"""Phiên bản luật chọn của bộ sinh; tăng khi đổi luật chọn dạng câu, cặp, phương án hay thứ tự (ADR-007 mục 2).

`v2`: dạng câu do RNG chọn trong các dạng khả thi thay vì do người gọi truyền vào; dạng ghép khái niệm loại thuật ngữ
mà khi điền vào chỗ trống của thân câu cho ra một trích dẫn của nhóm ứng viên; luật điền thử xét hai chiều (đoạn đã
điền nằm trong, hoặc chứa ở vị trí chạm thuật ngữ vừa điền, một trích dẫn của nhóm ứng viên) và hai dạng có phương án là
thuật ngữ loại theo khái niệm; mọi phép so xung đột bỏ khoảng trắng và dấu câu ở hai đầu đoạn được so, và dạng chọn
định nghĩa có luật điền thử riêng (điền tên khái niệm được hỏi vào phương án) (các thay đổi luật này gộp vào `v2` khi
nhánh chưa merge)."""

BLANK = "____"
NOT_ENOUGH_EVIDENCE = "không đủ căn cứ trong Vault"
DISTRACTOR_COUNT = 3


def _max_length(model: type[StrictModel], field: str) -> int:
    """Giới hạn `max_length` khai báo trên một trường của model, để không chép cứng con số của `domain`."""
    for constraint in model.model_fields[field].metadata:
        limit = getattr(constraint, "max_length", None)
        if isinstance(limit, int):
            return limit
    raise LookupError(f"{model.__name__}.{field} không khai báo max_length")


MAX_RATIONALE_CHARS = _max_length(MisconceptionRef, "rationale_vi")
"""Độ dài tối đa của `MisconceptionRef.rationale_vi`, đọc từ ràng buộc của model."""

_SAME_CHAPTER_LABEL = "Khái niệm khác cùng chương"
_SAME_SUBJECT_LABEL = "Khái niệm khác cùng môn"


FORM_ORDER: tuple[TheoryQuestionForm, ...] = (
    TheoryQuestionForm.CONCEPT_MATCH,
    TheoryQuestionForm.FILL_BLANK,
    TheoryQuestionForm.DEFINITION_CHOICE,
)
"""Thứ tự khai báo cố định của các dạng câu khi RNG rút dạng câu (I4).

Việc RNG của bộ ba I4 chọn dạng câu đã duyệt 2026-10-26; riêng thứ tự khai báo này là đề xuất, chờ duyệt (spec §5.11,
§17 D8). Đổi thứ tự phải tăng `GENERATOR_VERSION`."""

_FORM_LABELS: dict[TheoryQuestionForm, str] = {
    TheoryQuestionForm.CONCEPT_MATCH: "ghép khái niệm",
    TheoryQuestionForm.FILL_BLANK: "điền khuyết",
    TheoryQuestionForm.DEFINITION_CHOICE: "chọn định nghĩa đúng",
}


def _fold_pieces(pieces: Sequence[tuple[str, bool]]) -> list[tuple[str, bool]]:
    """Như `_match_key` của chuỗi ghép từ `pieces`, nhưng giữ cho từng ký tự cờ của mảnh sinh ra nó.

    Các bước: NFC từng mảnh, `casefold`, gộp mỗi dãy khoảng trắng (`str.isspace`, cùng tập ký tự với `\\s` của `re` mà
    `normalize_quote` dùng) thành một dấu cách mang cờ `True` nếu một ký tự trong dãy mang cờ `True`, rồi bỏ khoảng
    trắng và dấu câu ở hai đầu. Ghép các mảnh đã NFC chỉ bằng NFC của cả chuỗi khi chỗ nối không ghép được ký tự;
    `_fills_pool_text` kiểm điều đó trước khi dùng cờ.
    """
    folded: list[tuple[str, bool]] = []
    for piece, flag in pieces:
        for char in _nfc(piece).casefold():
            if not char.isspace():
                folded.append((char, flag))
            elif folded and folded[-1][0] == " ":
                folded[-1] = (" ", folded[-1][1] or flag)
            else:
                folded.append((" ", flag))
    start, end = 0, len(folded)
    while start < end and _is_edge_char(folded[start][0]):
        start += 1
    while end > start and _is_edge_char(folded[end - 1][0]):
        end -= 1
    return folded[start:end]


# --------------------------------------------------------------------------- #
# Nhóm ứng viên, pool_sha256 và RNG (ADR-007 mục 2)
# --------------------------------------------------------------------------- #


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def _pool_record(pair: ConceptPair) -> dict[str, str]:
    """Đúng năm trường của ADR-007 mục 2, mỗi trường chuẩn hoá NFC."""
    return {
        "subject_code": _nfc(pair.subject_code),
        "chapter_code": _nfc(pair.chapter_code),
        "term": _nfc(pair.term),
        "definition_quote": _nfc(pair.definition_quote),
        "chunk_id": _nfc(pair.chunk_id),
    }


def _pool_order(pair: ConceptPair) -> tuple[str, str, str, str]:
    """Sắp theo `(chapter_code, term, chunk_id, definition_quote)` sau NFC như ADR-007 mục 2; khoá cuối chỉ phân xử
    khi hoà, để thứ tự (và do đó `pool_sha256`) không phụ thuộc thứ tự đầu vào.

    Khoá phân xử `definition_quote` thuộc cách tính của ADR-007 mục 2 (duyệt 2026-10-25).
    """
    record = _pool_record(pair)
    return (record["chapter_code"], record["term"], record["chunk_id"], record["definition_quote"])


def candidate_pool(pairs: Sequence[ConceptPair], subject_code: str) -> list[ConceptPair]:
    """Cặp đã xác nhận cùng `subject_code` với slot, theo thứ tự chuẩn của `pool_sha256`."""
    selected = [
        pair for pair in pairs if pair.status is ConceptPairStatus.CONFIRMED and pair.subject_code == subject_code
    ]
    return sorted(selected, key=_pool_order)


def pool_sha256(pairs: Sequence[ConceptPair], subject_code: str) -> str:
    """sha256 hex của nhóm ứng viên theo đúng cách tính ở ADR-007 mục 2."""
    records = [_pool_record(pair) for pair in candidate_pool(pairs, subject_code)]
    payload = json.dumps(records, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def theory_rng(pool_digest: str, seed: int) -> random.Random:
    """Nguồn ngẫu nhiên duy nhất của bộ sinh (I4)."""
    return random.Random(f"{GENERATOR_VERSION}:{pool_digest}:{seed}")


# --------------------------------------------------------------------------- #
# Che tên khái niệm, trích dẫn
# --------------------------------------------------------------------------- #


def mask_terms(text: str, terms: Sequence[str]) -> str:
    """Thay mọi lần xuất hiện của từng `term` trong `text` (không phân biệt hoa thường, đúng ranh giới chữ, như
    `find_term_spans`) bằng `BLANK`; các vị trí chồng nhau được gộp. Phần còn lại giữ nguyên văn.

    Giới hạn: chỉ khớp đúng chuỗi `term`; dạng biến hình hay số nhiều ("Process" trong "Processes") không bị che. Cố ý
    không tách từ hay biến hình theo ngôn ngữ để giữ hàm thuần và không rẽ nhánh theo ngôn ngữ của tài liệu.
    """
    return _replace_terms(text, terms, BLANK)


def _merged_spans(text: str, terms: Sequence[str]) -> list[tuple[int, int]]:
    """Vị trí của mọi `term` trong `text` (như `find_term_spans`), sắp tăng dần, các vị trí chồng nhau được gộp."""
    spans = sorted(span for term in terms for span in find_term_spans(term, text))
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _replace_terms(text: str, terms: Sequence[str], replacement: str) -> str:
    """Như `mask_terms` nhưng thay bằng `replacement` tuỳ ý."""
    pieces: list[str] = []
    cursor = 0
    for start, end in _merged_spans(text, terms):
        pieces.append(text[cursor:start])
        pieces.append(replacement)
        cursor = end
    pieces.append(text[cursor:])
    return "".join(pieces)


def _citation(pair: ConceptPair, chunks: Mapping[str, KnowledgeChunk]) -> Citation:
    """Trích dẫn của cặp: `definition_quote` nguyên văn, kiểm lại I3 với chunk nguồn trước khi dùng.

    `page_number` là trang đầu của chunk (chunk không lưu ranh giới trang bên trong `raw_text`).
    """
    chunk = chunks.get(pair.chunk_id)
    if chunk is None:
        raise ValueError(f"thiếu chunk {pair.chunk_id} của cặp khái niệm {pair.pair_id}")
    if chunk.subject_code != pair.subject_code:
        raise ValueError(f"cặp khái niệm {pair.pair_id} và chunk {chunk.chunk_id} khác môn")
    check_definition_quote(chunk.chunk_id, chunk.raw_text, pair.definition_quote)
    return Citation(
        doc_id=chunk.doc_id,
        page_number=chunk.page_number,
        verbatim_quote=pair.definition_quote,
        chunk_id=chunk.chunk_id,
    )


def _rationale(pair: ConceptPair) -> str:
    """Giải thích phương án nhiễu: định nghĩa nguyên văn của khái niệm ứng với phương án (ADR-007 mục 1), kèm tên
    khái niệm khi còn chỗ."""
    labelled = f"«{pair.term}»: {pair.definition_quote}"
    return labelled if len(labelled) <= MAX_RATIONALE_CHARS else pair.definition_quote


# --------------------------------------------------------------------------- #
# Bản nháp câu, phương án nhiễu hợp lệ và ghép câu
# --------------------------------------------------------------------------- #

_Distractor = tuple[ConceptPair, str, str]  # (cặp, mã misconception, văn bản phương án)


class _Draft(NamedTuple):
    """Một câu có thể ghép ở một dạng câu: cặp đáp án, thân câu, đáp án, giải thích và luật chọn phương án nhiễu."""

    anchor: ConceptPair
    stem: str
    correct_text: str
    explanation: str
    excluded_concepts: frozenset[str]
    option_text: Callable[[ConceptPair], str]
    also_correct: Callable[[ConceptPair, str], bool]
    allow_other_chapters: bool
    conflicts_by_concept: bool


def _concept_conflict_checker(draft: _Draft, pool: Sequence[ConceptPair]) -> Callable[[ConceptPair, str], bool]:
    """Luật "đúng một đáp án" áp cho một cặp ứng viên làm phương án nhiễu của bản nháp.

    Dạng có phương án là định nghĩa (`conflicts_by_concept` sai): xét đúng cặp đó, `also_correct(cặp, văn bản)`. Dạng có
    phương án là thuật ngữ: đáp án đúng xác định theo khái niệm, nên cặp bị loại khi **bất kỳ** cặp nào cùng khái niệm
    (khoá `_option_key(term)`) trong nhóm ứng viên xung đột với đáp án. Kết quả theo khái niệm được nhớ lại trong một lần
    gọi `_eligible_distractors`; chỉ đọc nhóm ứng viên, không dùng RNG.
    """
    if not draft.conflicts_by_concept:
        return draft.also_correct
    by_concept: dict[str, list[ConceptPair]] = {}
    for member in pool:
        by_concept.setdefault(_option_key(member.term), []).append(member)
    verdicts: dict[str, bool] = {}

    def conflict(pair: ConceptPair, _text: str) -> bool:
        concept = _option_key(pair.term)
        if concept not in verdicts:
            members = by_concept[concept]
            verdicts[concept] = any(draft.also_correct(member, draft.option_text(member)) for member in members)
        return verdicts[concept]

    return conflict


def _eligible_distractors(
    draft: _Draft, pool: Sequence[ConceptPair], *, limit: int | None = None
) -> tuple[list[_Distractor], list[_Distractor]]:
    """Phương án nhiễu hợp lệ của bản nháp, chia thành `(cùng chương, chương khác cùng môn)`.

    Duyệt nhóm ứng viên theo thứ tự chuẩn, cặp cùng chương trước. Cặp bị bỏ khi khái niệm hoặc văn bản phương án (sau
    chuẩn hoá) trùng đáp án hay trùng một phương án đã nhận trước nó, hoặc khi luật "đúng một đáp án" loại nó (phương án
    đó cũng là một đáp án đúng; ở hai dạng có phương án là thuật ngữ luật xét cả khái niệm, xem
    `_concept_conflict_checker`). Kết quả chỉ phụ thuộc nhóm ứng viên và bản nháp, không phụ thuộc RNG, nên tính khả
    thi của một dạng câu được xác định trước khi rút số ngẫu nhiên nào. `limit` dừng sớm khi đã nhận đủ số phương án;
    phần đã nhận luôn là tiền tố của kết quả đầy đủ.

    Luật tham lam: không tìm bộ phương án lớn nhất, nên có thể báo thiếu phương án dù tồn tại bộ 3 hợp lệ (xem "Giới hạn
    đã biết của luật tham lam" ở docstring của module).
    """
    chapter_code = draft.anchor.chapter_code
    ordered = [pair for pair in pool if pair.chapter_code == chapter_code]
    if draft.allow_other_chapters:
        ordered.extend(pair for pair in pool if pair.chapter_code != chapter_code)
    also_correct = _concept_conflict_checker(draft, pool)
    concepts = set(draft.excluded_concepts)
    options = {_option_key(draft.correct_text)}
    same_chapter: list[_Distractor] = []
    other_chapters: list[_Distractor] = []
    for pair in ordered:
        if limit is not None and len(same_chapter) + len(other_chapters) >= limit:
            break
        text = draft.option_text(pair)
        concept, option = _option_key(pair.term), _option_key(text)
        if concept in concepts or option in options or also_correct(pair, text):
            continue
        concepts.add(concept)
        options.add(option)
        if pair.chapter_code == chapter_code:
            same_chapter.append((pair, THEORY_SAME_CHAPTER_CONCEPT, text))
        else:
            other_chapters.append((pair, THEORY_SAME_SUBJECT_CONCEPT, text))
    return same_chapter, other_chapters


def _is_feasible(draft: _Draft, pool: Sequence[ConceptPair]) -> bool:
    """Bản nháp có đủ 3 phương án nhiễu hợp lệ."""
    same_chapter, other_chapters = _eligible_distractors(draft, pool, limit=DISTRACTOR_COUNT)
    return len(same_chapter) + len(other_chapters) >= DISTRACTOR_COUNT


def _assemble(
    rng: random.Random,
    *,
    form: TheoryQuestionForm,
    draft: _Draft,
    distractors: Sequence[_Distractor],
    subject_code: str,
    seed: int,
    pool_digest: str,
    chunks: Mapping[str, KnowledgeChunk],
) -> TheoryQuestion:
    entries: list[tuple[str, _Distractor | None]] = [(draft.correct_text, None)]
    entries.extend((text, (pair, code, text)) for pair, code, text in distractors)
    rng.shuffle(entries)
    options: dict[OptionKey, str] = {}
    misconceptions: dict[OptionKey, MisconceptionRef] = {}
    citations: dict[OptionKey, Citation] = {}
    correct_key = OptionKey.A
    for key, (text, distractor) in zip(OptionKey, entries, strict=True):
        options[key] = text
        if distractor is None:
            correct_key = key
            continue
        pair, code, _ = distractor
        label = _SAME_CHAPTER_LABEL if code == THEORY_SAME_CHAPTER_CONCEPT else _SAME_SUBJECT_LABEL
        misconceptions[key] = MisconceptionRef(misconception_code=code, label_vi=label, rationale_vi=_rationale(pair))
        citations[key] = _citation(pair, chunks)
    return TheoryQuestion(
        form=form,
        subject_code=subject_code,
        chapter_code=draft.anchor.chapter_code,
        seed=seed,
        generator_version=GENERATOR_VERSION,
        pool_sha256=pool_digest,
        stem=draft.stem,
        options=options,
        correct_key=correct_key,
        distractor_to_misconception_map=misconceptions,
        distractor_citations=citations,
        solution_explanation_vi=draft.explanation,
        citation=_citation(draft.anchor, chunks),
    )


def _definition_explanation(pair: ConceptPair) -> str:
    return f"Định nghĩa của «{pair.term}» trong tài liệu: “{pair.definition_quote}”"


def _quote_conflict(anchor: ConceptPair) -> Callable[[ConceptPair, str], bool]:
    """Cặp có trích dẫn trùng, chứa hoặc nằm trong trích dẫn của cặp đáp án cũng là đáp án đúng: không làm nhiễu.

    Giống `_quotes_overlap(pair.definition_quote, anchor.definition_quote)` (so theo `_match_key`, bỏ dấu câu ở hai
    đầu); khoá của trích dẫn đáp án chỉ tính một lần.
    """
    anchor_key = _match_key(anchor.definition_quote)

    def conflict(pair: ConceptPair, _text: str) -> bool:
        pair_key = _match_key(pair.definition_quote)
        return pair_key in anchor_key or anchor_key in pair_key

    return conflict


def _fills_pool_text(quote: str, hidden: Sequence[str], text: str, pool_keys: Sequence[str]) -> bool:
    """Điền `text` vào mọi chỗ che các thuật ngữ `hidden` trong `quote` cho ra một đoạn khớp trích dẫn của nhóm ứng viên
    (`pool_keys`, đã qua `_match_key`), xét hai chiều: khi đó `text` cũng là một đáp án đúng.

    Đoạn đã điền được so theo `_match_key` (NFC, gộp khoảng trắng, không phân biệt hoa thường, bỏ khoảng trắng và dấu
    câu ở hai đầu), nên dấu chấm cuối thân câu hay cuối trích dẫn không làm trượt phép so. Một chỗ điền **làm đổi văn
    bản** khi khoá `_option_key` của văn bản gốc ở chỗ che khác khoá của `text`; điền lại đúng thuật ngữ vốn ở đó (dạng
    chọn định nghĩa che cả tên khái niệm được hỏi) không đổi gì nên không phải căn cứ.

    - Đoạn đã điền nằm trong một trích dẫn của nhóm ứng viên, khi có ít nhất một chỗ điền làm đổi văn bản.
    - Một trích dẫn của nhóm ứng viên xuất hiện trong đoạn đã điền ở vị trí phủ ít nhất một ký tự của một chỗ điền làm
      đổi văn bản. Trích dẫn chỉ nằm trong phần giữ nguyên của `quote` (ví dụ "phương thức tồn tại của vật chất" trong
      "____ là phương thức tồn tại của vật chất.") xuất hiện như nhau với mọi thuật ngữ nên không phải căn cứ để loại
      `text`.

    Vị trí chỗ điền được theo dõi bằng cờ trên từng ký tự (`_fold_pieces`), không dùng ký tự thay chỗ; cờ chỉ dựng khi
    có trích dẫn nằm trong đoạn đã điền. Cờ chỉ đúng khi NFC của từng mảnh ghép lại vẫn là NFC (chỗ nối không ghép được
    ký tự kết hợp với ký tự đứng trước; khi đó chuỗi ghép bằng NFC của cả chuỗi, nên cũng bằng `filled`); khi không
    đúng, hàm coi `text` là xung đột (loại thừa còn hơn ra câu hai đáp án đúng).
    """
    text_key = _option_key(text)
    pieces: list[tuple[str, bool]] = []
    cursor = 0
    for start, end in _merged_spans(quote, hidden):
        pieces.append((quote[cursor:start], False))
        pieces.append((text, _option_key(quote[start:end]) != text_key))
        cursor = end
    pieces.append((quote[cursor:], False))
    if not any(changed for _, changed in pieces):
        return False
    filled = _match_key("".join(piece for piece, _ in pieces))
    if any(filled in pool_key for pool_key in pool_keys):
        return True
    contained = [pool_key for pool_key in pool_keys if pool_key and pool_key in filled]
    if not contained:
        return False
    # Chỉ dựng cờ từng ký tự khi có trích dẫn nằm trong đoạn đã điền (chiều thứ hai).
    if not unicodedata.is_normalized("NFC", "".join(_nfc(piece) for piece, _ in pieces)):
        return True
    folded = _fold_pieces(pieces)
    marked = "".join(char for char, _ in folded)
    changed_at = [changed for _, changed in folded]
    for pool_key in contained:
        start = marked.find(pool_key)
        while start != -1:
            if any(changed_at[start : start + len(pool_key)]):
                return True
            start = marked.find(pool_key, start + 1)
    return False


def _concept_match_conflict(anchor: ConceptPair, pool_keys: Sequence[str]) -> Callable[[ConceptPair, str], bool]:
    """Quy tắc "đúng một đáp án" của dạng ghép khái niệm.

    Cặp xung đột khi trích dẫn của nó lồng với trích dẫn đáp án, hoặc khi thuật ngữ của nó điền vào chỗ `____` của thân
    câu (chỗ che `anchor.term` trong `anchor.definition_quote`) khớp một phần tử của `pool_keys` theo hai chiều của
    `_fills_pool_text`, cùng cách so như dạng điền khuyết. `_concept_conflict_checker` loại cả khái niệm của cặp xung
    đột. Ví dụ: "Không gian là hình thức tồn tại của vật chất." và "Thời gian là hình thức tồn tại của vật chất."
    đều trong nhóm ứng viên thì "Thời gian" cũng điền đúng chỗ trống của câu hỏi về "Không gian". Trích dẫn không
    chứa `anchor.term` (thân câu không có chỗ che, xem giới hạn che tên khái niệm) thì không có chỗ điền nào, chỉ còn
    luật trích dẫn lồng nhau.
    """
    quote_conflict = _quote_conflict(anchor)

    def conflict(pair: ConceptPair, text: str) -> bool:
        if quote_conflict(pair, text):
            return True
        return _fills_pool_text(anchor.definition_quote, [anchor.term], text, pool_keys)

    return conflict


def _fill_blank_conflict(
    anchor: ConceptPair, hidden: str, pool_keys: Sequence[str]
) -> Callable[[ConceptPair, str], bool]:
    """Quy tắc tương đương cho phương án là thuật ngữ (dạng điền khuyết).

    Cặp xung đột khi trích dẫn của nó lồng với trích dẫn đáp án, khi thuật ngữ của nó trùng/chứa/nằm trong thuật ngữ bị
    che, hoặc khi thuật ngữ đó điền vào chỗ trống khớp một phần tử của `pool_keys` theo hai chiều của
    `_fills_pool_text`. `_concept_conflict_checker` loại cả khái niệm của cặp xung đột.
    """
    quote_conflict = _quote_conflict(anchor)

    def conflict(pair: ConceptPair, text: str) -> bool:
        if quote_conflict(pair, text) or _overlaps(text, hidden):
            return True
        return _fills_pool_text(anchor.definition_quote, [hidden], text, pool_keys)

    return conflict


def _definition_choice_conflict(anchor: ConceptPair, pool_keys: Sequence[str]) -> Callable[[ConceptPair, str], bool]:
    """Quy tắc "đúng một đáp án" của dạng chọn định nghĩa (phương án là định nghĩa của từng cặp, xét đúng cặp đó).

    Cặp xung đột khi trích dẫn của nó lồng với trích dẫn đáp án, hoặc khi điền `anchor.term` vào mọi chỗ `____` của
    phương án (chỗ che `pair.term` và `anchor.term` trong `pair.definition_quote`, như `_masked_definition`) khớp một
    phần tử của `pool_keys` theo hai chiều của `_fills_pool_text`. Ví dụ hỏi định nghĩa của "Không gian" thì phương án
    "____ là hình thức tồn tại của vật chất" (của "Thời gian") điền "Không gian" ra "Không gian là hình thức tồn tại
    của vật chất", nằm trong trích dẫn "Không gian là hình thức tồn tại của vật chất xét về quảng tính." của đáp án:
    phương án đó cũng đúng.
    """
    quote_conflict = _quote_conflict(anchor)

    def conflict(pair: ConceptPair, text: str) -> bool:
        if quote_conflict(pair, text):
            return True
        return _fills_pool_text(pair.definition_quote, [pair.term, anchor.term], anchor.term, pool_keys)

    return conflict


def _pool_keys(pool: Sequence[ConceptPair]) -> list[str]:
    """Văn bản dùng để nhận ra đáp án đúng thứ hai qua luật điền thử (`_fills_pool_text`): `_match_key` của
    `definition_quote` của các cặp trong nhóm ứng viên, tức đúng dữ liệu đã nằm trong `pool_sha256`. `raw_text` của
    chunk không được dùng: chunk không thuộc `pool_sha256`, nên nếu dùng thì thêm một chunk cùng môn sẽ làm câu đổi dù
    `(generator_version, pool_sha256, seed)` giữ nguyên (I4)."""
    return [_match_key(pair.definition_quote) for pair in pool]


def _fill_blank_candidates(
    anchors: Sequence[ConceptPair], pool: Sequence[ConceptPair]
) -> Iterator[tuple[ConceptPair, str]]:
    """`(cặp chứa trích dẫn, term bị che)`: term là `term` của một khái niệm khác đã xác nhận và xuất hiện trong
    trích dẫn; mỗi khái niệm chỉ một lần cho mỗi trích dẫn, theo thứ tự chuẩn. Sinh lười, theo đúng thứ tự đó."""
    for anchor in anchors:
        seen = {_option_key(anchor.term)}
        for other in pool:
            concept = _option_key(other.term)
            if concept in seen or not find_term_spans(other.term, anchor.definition_quote):
                continue
            seen.add(concept)
            yield anchor, other.term


def _term_of(pair: ConceptPair) -> str:
    return pair.term


def _masked_definition(asked_term: str) -> Callable[[ConceptPair], str]:
    """Phương án dạng định nghĩa: che tên khái niệm của chính nó và tên khái niệm đang hỏi (ADR-007 mục 1)."""

    def text(pair: ConceptPair) -> str:
        return mask_terms(pair.definition_quote, [pair.term, asked_term])

    return text


def _drafts(form: TheoryQuestionForm, anchors: Sequence[ConceptPair], pool: Sequence[ConceptPair]) -> Iterator[_Draft]:
    """Mọi bản nháp của một dạng câu trên các cặp neo, theo thứ tự chuẩn (chưa kiểm phương án nhiễu).

    Sinh lười: chỉ dựng bản nháp khi được đọc tới, nên kiểm tính khả thi dừng ở bản nháp khả thi đầu tiên và chỉ dạng
    đã được rút mới dựng đủ mọi bản nháp. Thứ tự và nội dung bản nháp không phụ thuộc cách đọc.
    """
    if form is TheoryQuestionForm.FILL_BLANK:
        pool_keys = _pool_keys(pool)
        yield from (
            _Draft(
                anchor=anchor,
                stem=(
                    "Điền thuật ngữ thích hợp vào chỗ trống trong đoạn trích sau:\n\n"
                    f"> {mask_terms(anchor.definition_quote, [hidden])}"
                ),
                correct_text=hidden,
                explanation=f"Thuật ngữ bị che là «{hidden}». Đoạn trích nguyên văn: “{anchor.definition_quote}”",
                excluded_concepts=frozenset({_option_key(anchor.term), _option_key(hidden)}),
                option_text=_term_of,
                also_correct=_fill_blank_conflict(anchor, hidden, pool_keys),
                allow_other_chapters=True,
                conflicts_by_concept=True,
            )
            for anchor, hidden in _fill_blank_candidates(anchors, pool)
        )
    elif form is TheoryQuestionForm.CONCEPT_MATCH:
        pool_keys = _pool_keys(pool)
        yield from (
            _Draft(
                anchor=anchor,
                stem=f"Khái niệm nào ứng với đoạn trích sau?\n\n> {mask_terms(anchor.definition_quote, [anchor.term])}",
                correct_text=anchor.term,
                explanation=_definition_explanation(anchor),
                excluded_concepts=frozenset({_option_key(anchor.term)}),
                option_text=_term_of,
                also_correct=_concept_match_conflict(anchor, pool_keys),
                allow_other_chapters=True,
                conflicts_by_concept=True,
            )
            for anchor in anchors
        )
    else:
        pool_keys = _pool_keys(pool)
        yield from (
            _Draft(
                anchor=anchor,
                stem=f"Phát biểu nào là định nghĩa của «{anchor.term}»?",
                correct_text=mask_terms(anchor.definition_quote, [anchor.term]),
                explanation=_definition_explanation(anchor),
                excluded_concepts=frozenset({_option_key(anchor.term)}),
                option_text=_masked_definition(anchor.term),
                also_correct=_definition_choice_conflict(anchor, pool_keys),
                allow_other_chapters=False,
                conflicts_by_concept=False,
            )
            for anchor in anchors
        )


def _anchors(pool: Sequence[ConceptPair], chapter_code: str | None) -> list[ConceptPair]:
    """Cặp có thể làm đáp án: cả nhóm ứng viên, hoặc chỉ các cặp thuộc chương của slot."""
    return [pair for pair in pool if chapter_code is None or pair.chapter_code == chapter_code]


def _feasible_forms(anchors: Sequence[ConceptPair], pool: Sequence[ConceptPair]) -> tuple[TheoryQuestionForm, ...]:
    """Các dạng câu có ít nhất một bản nháp đủ 3 phương án nhiễu, theo `FORM_ORDER`.

    Mỗi dạng chỉ dựng bản nháp tới bản khả thi đầu tiên; kết quả giống hệt khi dựng đủ rồi mới kiểm.
    """
    return tuple(
        form for form in FORM_ORDER if any(_is_feasible(draft, pool) for draft in _drafts(form, anchors, pool))
    )


def _feasible_drafts(
    form: TheoryQuestionForm, anchors: Sequence[ConceptPair], pool: Sequence[ConceptPair]
) -> list[_Draft]:
    """Mọi bản nháp khả thi của một dạng câu, theo thứ tự chuẩn; chỉ gọi cho dạng đã được RNG rút."""
    return [draft for draft in _drafts(form, anchors, pool) if _is_feasible(draft, pool)]


def _infeasible_reason(form: TheoryQuestionForm, anchors: Sequence[ConceptPair], pool: Sequence[ConceptPair]) -> str:
    has_drafts = next(_drafts(form, anchors, pool), None) is not None
    if form is TheoryQuestionForm.FILL_BLANK and not has_drafts:
        detail = "không có trích dẫn định nghĩa nào chứa thuật ngữ của một khái niệm khác đã xác nhận"
    elif form is TheoryQuestionForm.FILL_BLANK:
        detail = "không chọn được 3 thuật ngữ khác nhau làm phương án nhiễu"
    elif form is TheoryQuestionForm.DEFINITION_CHOICE:
        detail = "không đủ 3 khái niệm khác cùng chương với định nghĩa khác nhau để làm phương án nhiễu"
    else:
        detail = "không chọn được 3 khái niệm khác nhau cùng môn làm phương án nhiễu"
    return f"{_FORM_LABELS[form]}: {detail}"


# --------------------------------------------------------------------------- #
# API của service
# --------------------------------------------------------------------------- #


def feasible_forms(
    pairs: Sequence[ConceptPair], subject_code: str, *, chapter_code: str | None = None
) -> tuple[TheoryQuestionForm, ...]:
    """Các dạng câu khả thi với nhóm ứng viên của môn và chương của slot, theo `FORM_ORDER`.

    Đây đúng là tập mà `generate_theory_question` rút dạng câu từ đó; hàm không dùng RNG.
    """
    pool = candidate_pool(pairs, subject_code)
    return _feasible_forms(_anchors(pool, chapter_code), pool)


def generate_theory_question(
    pairs: Sequence[ConceptPair],
    chunks: Mapping[str, KnowledgeChunk],
    *,
    subject_code: str,
    seed: int,
    blueprint_id: str,
    slot_index: int,
    topic_key: str | None,
    chapter_code: str | None = None,
) -> TheoryQuestion | ReviewTicket:
    """Sinh một câu lý thuyết MCQ4, hoặc `ReviewTicket` khi Vault không đủ căn cứ.

    `pairs` có thể gồm cặp của môn khác hay chưa xác nhận; chúng bị bỏ qua. `chunks` ánh xạ `chunk_id` → chunk nguồn
    của các cặp. `chapter_code` (tuỳ chọn) là chương của slot: giới hạn cặp đáp án vào chương đó; phương án nhiễu vẫn
    có thể lấy cùng môn. `blueprint_id`, `slot_index`, `topic_key` chỉ dùng để ghi `ReviewTicket`.

    Dạng câu không phải đầu vào (I4): RNG duy nhất `random.Random(f"{generator_version}:{pool_sha256}:{seed}")` rút
    lần lượt dạng câu trong `feasible_forms(...)` (dạng không đủ 3 phương án nhiễu bị loại trước), bản nháp trong các
    bản nháp khả thi của dạng đó, thứ tự phương án nhiễu hợp lệ, rồi thứ tự bốn phương án. Không dạng nào khả thi ⇒
    `ReviewTicket`. RNG của bộ ba I4 chọn dạng câu là phần đã duyệt 2026-10-26; thứ tự rút (dạng trước, cặp sau), thứ
    tự `FORM_ORDER`, định nghĩa "khả thi" và `ReviewTicket` khi không dạng nào khả thi là đề xuất, chờ duyệt (spec §5.11,
    §17 D8).

    Cặp được chọn mà thiếu chunk, khác môn với chunk hoặc trích dẫn không còn khớp nguyên văn chunk (I3) ⇒ lỗi
    (`ValueError`/`QuoteMismatchError`): đó là dữ liệu hỏng, không phải thiếu căn cứ.
    """
    if not 0 <= seed < SEED_SPACE:
        raise ValueError(f"seed phải trong [0, 2**63): {seed}")
    pool = candidate_pool(pairs, subject_code)
    digest = pool_sha256(pairs, subject_code)
    rng = theory_rng(digest, seed)

    def ticket(detail: str) -> ReviewTicket:
        return ReviewTicket(
            blueprint_id=blueprint_id,
            slot_index=slot_index,
            topic_key=topic_key,
            reason=f"{NOT_ENOUGH_EVIDENCE}: {detail}",
            attempts=[],
        )

    anchors = _anchors(pool, chapter_code)
    if not anchors:
        scope = f"chương {chapter_code} của môn {subject_code}" if chapter_code else f"môn {subject_code}"
        return ticket(f"không có cặp khái niệm đã xác nhận nào thuộc {scope}")

    forms = _feasible_forms(anchors, pool)
    if not forms:
        reasons = "; ".join(_infeasible_reason(form, anchors, pool) for form in FORM_ORDER)
        return ticket(f"không dạng câu nào khả thi ({reasons})")

    form = rng.choice(forms)
    draft = rng.choice(_feasible_drafts(form, anchors, pool))
    same_chapter, other_chapters = _eligible_distractors(draft, pool)
    rng.shuffle(same_chapter)
    rng.shuffle(other_chapters)
    return _assemble(
        rng,
        form=form,
        draft=draft,
        distractors=(same_chapter + other_chapters)[:DISTRACTOR_COUNT],
        subject_code=subject_code,
        seed=seed,
        pool_digest=digest,
        chunks=chunks,
    )


def generate_theory_question_from_vault(
    vault: VaultRepository,
    *,
    subject_code: str,
    seed: int,
    blueprint_id: str,
    slot_index: int,
    topic_key: str | None,
    chapter_code: str | None = None,
) -> TheoryQuestion | ReviewTicket:
    """Như `generate_theory_question`, với cặp đã xác nhận của môn `subject_code` đọc từ Vault.

    Chỉ đọc chunk nguồn của các cặp đó, và chỉ để dựng và kiểm lại trích dẫn (I3); chunk khác của môn không ảnh hưởng
    tới câu sinh ra (I4).
    """
    pairs = vault.list_concept_pairs(subject_code, status=ConceptPairStatus.CONFIRMED)
    sources = {pair.chunk_id for pair in pairs}
    chunks = {chunk.chunk_id: chunk for chunk in vault.list_chunks(subject_code) if chunk.chunk_id in sources}
    return generate_theory_question(
        pairs,
        chunks,
        subject_code=subject_code,
        seed=seed,
        blueprint_id=blueprint_id,
        slot_index=slot_index,
        topic_key=topic_key,
        chapter_code=chapter_code,
    )
