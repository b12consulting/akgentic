#!/usr/bin/env bash
set -euo pipefail

# Downloads all Akgentic packages from GitHub and builds a single archive
# with the complete workspace structure.
# Requires: gh (GitHub CLI) authenticated with access to b12consulting repos.

BRANCH="${1:-master}"
ORG="b12consulting"

REPOS=(
  akgentic-quick-start
  akgentic-core
  akgentic-llm
  akgentic-tool
  akgentic-agent
  akgentic-catalog
  akgentic-infra
  akgentic-team
  akgentic-template
)

WORK_DIR=$(mktemp -d)
OUTPUT_DIR="${WORK_DIR}/akgentic-quick-start"
OUTPUT_ZIP="akgentic-complete-${BRANCH}.zip"

cleanup() { rm -rf "${WORK_DIR}"; }
trap cleanup EXIT

echo "==> Downloading ${#REPOS[@]} repositories (branch: ${BRANCH})..."

# Download all zips using gh api (handles private repo auth)
FAILED=()
for repo in "${REPOS[@]}"; do
  echo "    Downloading ${repo}..."
  if ! gh api "repos/${ORG}/${repo}/zipball/${BRANCH}" \
       > "${WORK_DIR}/${repo}.zip" 2>/dev/null; then
    echo "    ⚠ FAILED: ${repo} (branch '${BRANCH}' not found or no access)"
    FAILED+=("${repo}")
  fi
done

if [[ ${#FAILED[@]} -gt 0 ]]; then
  echo ""
  echo "ERROR: Failed to download: ${FAILED[*]}"
  echo "Check that the repos exist and you have access (gh auth status)."
  exit 1
fi

echo "==> All downloads complete."

# Extract quick-start as the root
echo "==> Extracting akgentic-quick-start as root..."
unzip -q "${WORK_DIR}/akgentic-quick-start.zip" -d "${WORK_DIR}/tmp-root"
# GitHub zipball directories are named <org>-<repo>-<sha>, find it dynamically
mv "${WORK_DIR}"/tmp-root/${ORG}-akgentic-quick-start-* "${OUTPUT_DIR}"
rm -rf "${WORK_DIR}/tmp-root"

# Remove the git submodule stubs (empty dirs or .gitmodules placeholders)
rm -rf "${OUTPUT_DIR}/packages"
mkdir -p "${OUTPUT_DIR}/packages"

# Extract each submodule package into packages/
for repo in "${REPOS[@]}"; do
  [[ "${repo}" == "akgentic-quick-start" ]] && continue

  pkg_name="${repo}"
  echo "    Extracting ${pkg_name} -> packages/${pkg_name}/"
  unzip -q "${WORK_DIR}/${repo}.zip" -d "${WORK_DIR}/tmp-pkg"
  mv "${WORK_DIR}"/tmp-pkg/${ORG}-${repo}-* "${OUTPUT_DIR}/packages/${pkg_name}"
  rm -rf "${WORK_DIR}/tmp-pkg"
done

# Build final archive
echo "==> Building ${OUTPUT_ZIP}..."
(cd "${WORK_DIR}" && zip -qr - "akgentic-quick-start") > "${OUTPUT_ZIP}"

echo "==> Done! Archive: ${OUTPUT_ZIP} ($(du -h "${OUTPUT_ZIP}" | cut -f1))"
