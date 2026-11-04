"""Kho tạm đề xuất đọc lại bằng AI của MỘT instance store (spec §6.10a "Lưu đề xuất, hết hiệu lực").

Không module-level, không bảng SQLite: mỗi store giữ một `AiRereadProposalCache` làm thuộc tính instance, nên đề xuất
mất khi khởi động lại. Tối đa `MAX_AI_REREAD_PROPOSALS` đề xuất; đầy ⇒ bỏ đề xuất cũ nhất. Có khoá vì handler chạy
trong threadpool.
"""

from __future__ import annotations

import threading
from collections import OrderedDict

from ..domain.ai_reread import MAX_AI_REREAD_PROPOSALS, AiRereadProposal, AiRereadProposalRecord


class AiRereadProposalCache:
    def __init__(self, max_entries: int = MAX_AI_REREAD_PROPOSALS) -> None:
        if max_entries < 1:
            raise ValueError("max_entries phải ≥ 1")
        self._max_entries = max_entries
        self._lock = threading.Lock()
        self._records: OrderedDict[str, AiRereadProposalRecord] = OrderedDict()

    def put(self, proposal: AiRereadProposal) -> None:
        """Thêm đề xuất `pending` (mới nhất); vượt trần ⇒ bỏ đề xuất cũ nhất."""
        with self._lock:
            self._records.pop(proposal.proposal_id, None)
            self._records[proposal.proposal_id] = AiRereadProposalRecord(proposal=proposal)
            while len(self._records) > self._max_entries:
                self._records.popitem(last=False)

    def get(self, proposal_id: str) -> AiRereadProposalRecord | None:
        with self._lock:
            return self._records.get(proposal_id)

    def discard(self, proposal_id: str) -> bool:
        """Đánh dấu `discarded`; `False` khi kho không có đề xuất này (chưa từng có, đã bị đẩy ra hoặc tài liệu đã
        xoá)."""
        with self._lock:
            record = self._records.get(proposal_id)
            if record is None:
                return False
            self._records[proposal_id] = record.model_copy(update={"state": "discarded"})
            return True

    def drop_document(self, document_id: str) -> None:
        """Bỏ mọi đề xuất của tài liệu (xoá tài liệu xoá đề xuất của nó)."""
        with self._lock:
            for proposal_id in [k for k, r in self._records.items() if r.proposal.document_id == document_id]:
                del self._records[proposal_id]

    def __len__(self) -> int:
        with self._lock:
            return len(self._records)
