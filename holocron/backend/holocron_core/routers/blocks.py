"""Cây mục, khối của mục, khối của trang và ảnh cắt (SPECIFICATION §6.10; ADR-010 mục 4, 13, 19; X09).

Bốn endpoint GET, chạy offline, CHỈ đọc, qua `LocalAccessMiddleware` như mọi request (§13.1). Cây và khối của mục chỉ
thấy khối đủ điều kiện của tài liệu ĐÃ ĐÓNG, lọc lúc truy vấn (`services/block_tree.py`). Tra cứu khối
(`GET /v1/vault/blocks/search`) thuộc X10, không ở đây.

Ảnh cắt: `crop_ref` sai mẫu ⇒ 422 (kiểm ở tham số đường dẫn); chỉ phục vụ khi CHÍNH tài liệu trong đường dẫn có hàng
`crop_images` trỏ tới `crop_ref` và file còn (kho kiểm), ngược lại 404; body là đúng byte đã lưu. Không nhận token qua
URL (token chỉ ở header, `LocalAccessMiddleware`).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, Response

from ..domain.blocks import CROP_REF_PATTERN
from ..domain.vault import SUBJECT_CODE_PATTERN, PageBlockList, SectionBlocks, SubjectTree
from ..services.block_tree import BlockTreeNotFoundError, page_block_list, section_blocks, subject_tree
from .deps import StoreDep, VaultDep

router = APIRouter()

SUBJECT_NOT_FOUND_DETAIL = "không tìm thấy môn"
DOCUMENT_NOT_FOUND_DETAIL = "không tìm thấy tài liệu"
SECTION_NOT_FOUND_DETAIL = "không tìm thấy tài liệu hoặc mục"
PAGE_NOT_FOUND_DETAIL = "không tìm thấy tài liệu hoặc trang"
CROP_NOT_FOUND_DETAIL = "không tìm thấy ảnh cắt của tài liệu"
DEFAULT_SECTION_LIMIT = 200
MAX_SECTION_LIMIT = 500
# Ảnh cắt định danh theo sha256 nội dung (`crop_ref`): bất biến, chỉ trình duyệt của người dùng giữ (ADR-010 mục 19).
CROP_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "private, max-age=31536000, immutable",
}


@router.get("/v1/vault/tree", response_model=SubjectTree)
def get_subject_tree(
    store: StoreDep, vault: VaultDep, subject_code: Annotated[str, Query(pattern=SUBJECT_CODE_PATTERN)]
) -> SubjectTree:
    try:
        return subject_tree(store, store, vault, subject_code)
    except BlockTreeNotFoundError as exc:
        raise HTTPException(status_code=404, detail=SUBJECT_NOT_FOUND_DETAIL) from exc


@router.get("/v1/documents/{document_id}/sections/{section_id}/blocks", response_model=SectionBlocks)
def get_section_blocks(
    store: StoreDep,
    vault: VaultDep,
    document_id: str,
    section_id: str,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=MAX_SECTION_LIMIT)] = DEFAULT_SECTION_LIMIT,
) -> SectionBlocks:
    try:
        return section_blocks(store, vault, document_id, section_id, offset=offset, limit=limit)
    except BlockTreeNotFoundError as exc:
        raise HTTPException(status_code=404, detail=SECTION_NOT_FOUND_DETAIL) from exc


@router.get("/v1/documents/{document_id}/pages/{page_number}/blocks", response_model=PageBlockList)
def get_page_blocks(store: StoreDep, document_id: str, page_number: int) -> PageBlockList:
    try:
        return page_block_list(store, document_id, page_number)
    except BlockTreeNotFoundError as exc:
        raise HTTPException(status_code=404, detail=PAGE_NOT_FOUND_DETAIL) from exc


@router.get(
    "/v1/documents/{document_id}/crops/{crop_ref}",
    response_class=Response,
    responses={200: {"content": {"image/png": {}}, "description": "Byte PNG của ảnh cắt"}},
)
def get_crop(store: StoreDep, document_id: str, crop_ref: Annotated[str, Path(pattern=CROP_REF_PATTERN)]) -> Response:
    if store.get_imported_document(document_id) is None:
        raise HTTPException(status_code=404, detail=DOCUMENT_NOT_FOUND_DETAIL)
    png = store.get_crop_bytes(document_id, crop_ref)
    if png is None:
        raise HTTPException(status_code=404, detail=CROP_NOT_FOUND_DETAIL)
    return Response(content=png, media_type="image/png", headers=CROP_HEADERS)
