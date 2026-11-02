#!/usr/bin/env bash
# Tự merge một PR vào develop khi đủ điều kiện người dùng đặt ngày 2026-10-29
# (.claude/state/decisions-answers-2026-10-29.md, câu 1 và mục (c)). Thiếu một điều kiện thì in lý do, thoát mã
# khác 0 và không làm gì thêm. Sửa file này phải qua người dùng (deny Edit/Write trong .claude/settings.json).
#
# Dùng: bash .claude/scripts/merge-to-develop.sh <số PR> <SHA head 40 hex>
set -euo pipefail

REQUIRED_JOBS=(
  "tests (ubuntu-latest, py3.12)"
  "tests (ubuntu-latest, py3.13)"
  "tests (windows-latest, py3.12)"
  "frontend (node 20)"
  "frontend (node 22)"
  "quality"
  "determinism"
  "commit-lint"
)

# File mà PR đụng vào thì KHÔNG tự merge (so trên đường dẫn đầy đủ trong repo, regex mở rộng).
PROTECTED_PATHS=(
  '^holocron/backend/holocron_core/sandbox/'
  '^holocron/backend/holocron_core/infrastructure/storage/migrations/'
  '^holocron/backend/holocron_core/routers/access\.py$'
  '^holocron/backend/holocron_core/routers/api\.py$'
  '^holocron/backend/holocron_core/routers/static_site\.py$'
  '^SPECIFICATION\.md$'
  '^\.claude/'
  '^\.github/'
  '(^|/)pyproject\.toml$'
  '(^|/)package\.json$'
  '(^|/)package-lock\.json$'
  '(^|/)requirements[^/]*\.txt$'
)

# Test có sẵn bị sửa/xoá/đổi tên thì KHÔNG tự merge (thêm file test mới thì được).
TEST_PATH='(^|/)tests/|\.test\.tsx?$'

REPO_ROOT="$(git rev-parse --show-toplevel)"
LOG_FILE="${REPO_ROOT}/.claude/state/merge-log.md"

refuse() {
  echo "TỪ CHỐI: $1" >&2
  exit 2
}

[[ $# -eq 2 ]] || refuse "cần đúng 2 tham số: <số PR> <SHA head>"
PR="$1"
EXPECTED_SHA="$2"
[[ "${PR}" =~ ^[0-9]+$ ]] || refuse "số PR phải chỉ gồm chữ số: '${PR}'"
[[ "${EXPECTED_SHA}" =~ ^[0-9a-f]{40}$ ]] || refuse "SHA phải đúng 40 ký tự hex thường: '${EXPECTED_SHA}'"

# Mỗi trường đọc bằng `gh … --jq` (jq tích hợp trong gh), không cần cài jq riêng.
pr_field() {
  gh pr view "${PR}" --json "$1" --jq "$2"
}

[[ "$(pr_field state '.state')" == "OPEN" ]] || refuse "PR #${PR} không ở trạng thái OPEN"
[[ "$(pr_field baseRefName '.baseRefName')" == "develop" ]] || refuse "base của PR #${PR} không phải develop"
[[ "$(pr_field isDraft '.isDraft')" == "false" ]] || refuse "PR #${PR} đang là nháp"
HEAD_SHA="$(pr_field headRefOid '.headRefOid')"
[[ "${HEAD_SHA}" == "${EXPECTED_SHA}" ]] || refuse "head của PR #${PR} là ${HEAD_SHA}, không khớp ${EXPECTED_SHA}"
[[ "$(pr_field mergeable '.mergeable')" == "MERGEABLE" ]] || refuse "PR #${PR} không mergeable (xung đột hoặc GitHub chưa tính xong)"

# Kết luận từng lượt chạy, dạng "tên<TAB>conclusion"; mọi lượt cùng tên phải SUCCESS và phải có ít nhất một lượt.
CHECKS="$(pr_field statusCheckRollup '.statusCheckRollup[] | "\(.name)\t\(.conclusion)"')"
for job in "${REQUIRED_JOBS[@]}"; do
  found=0
  while IFS=$'\t' read -r name conclusion; do
    [[ "${name}" == "${job}" ]] || continue
    found=1
    [[ "${conclusion}" == "SUCCESS" ]] || refuse "job '${job}' có conclusion = ${conclusion}, cần SUCCESS"
  done <<<"${CHECKS}"
  [[ "${found}" -eq 1 ]] || refuse "thiếu job bắt buộc '${job}'"
done

FILES="$(gh api "repos/{owner}/{repo}/pulls/${PR}/files" --paginate --jq '.[] | "\(.status)\t\(.filename)\t\(.previous_filename // "")"')"
[[ -n "${FILES}" ]] || refuse "không đọc được danh sách file của PR #${PR}"

while IFS=$'\t' read -r status path previous; do
  for pattern in "${PROTECTED_PATHS[@]}"; do
    if [[ "${path}" =~ ${pattern} ]] || { [[ -n "${previous}" ]] && [[ "${previous}" =~ ${pattern} ]]; }; then
      refuse "PR #${PR} đụng file cần người dùng duyệt: ${path}"
    fi
  done
  if [[ "${status}" != "added" ]]; then
    if [[ "${path}" =~ ${TEST_PATH} ]] || { [[ -n "${previous}" ]] && [[ "${previous}" =~ ${TEST_PATH} ]]; }; then
      refuse "PR #${PR} sửa/xoá test có sẵn (${status}): ${path}"
    fi
  fi
done <<<"${FILES}"

gh pr merge "${PR}" --merge --match-head-commit "${EXPECTED_SHA}"

MERGE_SHA="$(gh pr view "${PR}" --json mergeCommit --jq '.mergeCommit.oid')"
STAMP="$(date '+%Y-%m-%d %H:%M:%S %z')"
JOBS_CHECKED="$(IFS=';'; echo "${REQUIRED_JOBS[*]}")"
printf -- '- %s | PR #%s | head %s | merge %s | jobs: %s\n' \
  "${STAMP}" "${PR}" "${EXPECTED_SHA}" "${MERGE_SHA}" "${JOBS_CHECKED}" >>"${LOG_FILE}"
echo "Đã merge PR #${PR} vào develop (${MERGE_SHA}); ghi ${LOG_FILE}."
