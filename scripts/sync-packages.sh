#!/bin/bash

ORG="b12consulting"
PARENT_DIR="packages"
BRANCH="master"
DEFAULT_PACKAGES=(core llm tool agent catalog team infra frontend)

usage() {
    cat <<EOF
Usage: $(basename "$0") [-h|--help] [-n|--dry-run] [--no-tag] [PACKAGE ...]

Sync akgentic-* packages from branch '$BRANCH' using git subtree pull, then
freeze each synced version by pushing a vX.Y.Z tag on the package's own repo.

Arguments:
  PACKAGE             Short package name (without the 'akgentic-' prefix).
                      May be repeated. If omitted, the default list is used.

Default packages:
  ${DEFAULT_PACKAGES[*]}

Options:
  -h, --help          Show this help message and exit.
  -n, --dry-run       Do NOT pull. For each package, fetch the remote and report
                      exactly what a real subtree sync would import: the commits
                      between the last-synced SHA (from git's subtree bookmark) and
                      remote '$BRANCH', plus the version delta. Flags the case where
                      commits are pending but the manifest version was not bumped.
                      Read-only.
      --no-tag        Sync only; do NOT push the vX.Y.Z freeze tag to the package
                      repos. By default a real sync tags each package's remote HEAD
                      with v<manifest-version>, failing if that tag already exists
                      at a different commit (a version reused for different code).

Examples:
  $(basename "$0")                    # sync the default package list (and tag)
  $(basename "$0") core llm           # sync only akgentic-core and akgentic-llm
  $(basename "$0") --dry-run          # show, package by package, what would be synced
  $(basename "$0") --no-tag           # sync without freezing version tags
EOF
}

DRY_RUN=0
NO_TAG=0
PACKAGES=()
for arg in "$@"; do
    case "$arg" in
        -h|--help)
            usage
            exit 0
            ;;
        -n|--dry-run)
            DRY_RUN=1
            ;;
        --no-tag)
            NO_TAG=1
            ;;
        -*)
            echo "❌ Unknown option: $arg" >&2
            usage >&2
            exit 2
            ;;
        *)
            PACKAGES+=("$arg")
            ;;
    esac
done

if [ "${#PACKAGES[@]}" -eq 0 ]; then
    PACKAGES=("${DEFAULT_PACKAGES[@]}")
fi

# Read the [project] version from a pyproject.toml at a path (tomllib; robust
# against comments/formatting). Prints the version, or "-" if absent.
read_project_version() {
    local pyproject="$1"
    [ -f "$pyproject" ] || { echo "-"; return; }
    python3 - "$pyproject" <<'PY'
import sys, tomllib
try:
    with open(sys.argv[1], "rb") as fh:
        proj = tomllib.load(fh).get("project", {})
    print(proj.get("version") or ("dynamic" if "version" in proj.get("dynamic", []) else "-"))
except Exception:
    print("-")
PY
}

# Read the "version" field from a package.json at a path. Prints it, or "-".
read_npm_version() {
    local pkgjson="$1"
    [ -f "$pkgjson" ] || { echo "-"; return; }
    python3 - "$pkgjson" <<'PY'
import sys, json
try:
    with open(sys.argv[1]) as fh:
        print(json.load(fh).get("version") or "-")
except Exception:
    print("-")
PY
}

# Vendored manifest version for a package dir (pyproject.toml, else package.json).
manifest_version() {
    local pkg_dir="$1" v
    v="$(read_project_version "$pkg_dir/pyproject.toml")"
    [ "$v" != "-" ] && { echo "$v"; return; }
    read_npm_version "$pkg_dir/package.json"
}

# Freeze a package's version on its OWN repo by pushing a vX.Y.Z tag at the remote
# '$BRANCH' HEAD (the commit just vendored into the framework). Idempotent when the
# tag already points at that SHA; FAILS LOUDLY (exit 1) on a collision — the tag
# exists but points elsewhere — so a version reused for different code is surfaced.
# Args: <pkg> <remote_url>
freeze_package_tag() {
    local pkg="$1" remote_url="$2"
    local version tag head_sha existing_sha
    version="$(manifest_version "$PARENT_DIR/$pkg")"

    if [ "$version" = "-" ] || [ "$version" = "dynamic" ]; then
        echo "  🏷️  $pkg: no static version in manifest — skipping tag" >&2
        return 0
    fi
    tag="v$version"

    # Remote HEAD of the synced branch = the commit we just vendored.
    head_sha="$(git ls-remote "$remote_url" "refs/heads/$BRANCH" | cut -f1)"
    if [ -z "$head_sha" ]; then
        echo "❌ $pkg: cannot resolve remote $BRANCH HEAD — tag $tag NOT created." >&2
        return 1
    fi

    # Does the tag already exist on the remote? Resolve the annotated/peeled SHA.
    existing_sha="$(git ls-remote "$remote_url" "refs/tags/$tag^{}" | cut -f1)"
    [ -z "$existing_sha" ] && existing_sha="$(git ls-remote "$remote_url" "refs/tags/$tag" | cut -f1)"

    if [ -n "$existing_sha" ]; then
        if [ "$existing_sha" = "$head_sha" ]; then
            echo "  🏷️  $pkg: $tag already frozen at ${head_sha:0:9} — ok"
            return 0
        fi
        echo "❌ $pkg: tag $tag already exists at ${existing_sha:0:9} but $BRANCH HEAD is" >&2
        echo "   ${head_sha:0:9} — version reused for different code. Bump the version" >&2
        echo "   in $pkg's pyproject.toml, or remove the stale tag. NOT overwriting." >&2
        return 1
    fi

    # Create the tag directly on the remote ref at HEAD (no local checkout needed).
    echo "  🏷️  $pkg: freezing $tag at ${head_sha:0:9} on $remote_url"
    if ! git push "$remote_url" "${head_sha}:refs/tags/$tag"; then
        echo "❌ $pkg: failed to push tag $tag to $remote_url." >&2
        return 1
    fi
}

# Read the vendored manifest version for a package (pyproject.toml, else the npm
# package.json). Prints "<version> [<manifest>]" so the report shows which file.
vendored_manifest_version() {
    local pkg_dir="$1" v
    v="$(read_project_version "$pkg_dir/pyproject.toml")"
    if [ "$v" != "-" ]; then echo "$v pyproject.toml"; return; fi
    echo "$(read_npm_version "$pkg_dir/package.json") package.json"
}

# Read the manifest version at a git revision (same manifest kind as vendored).
# Args: <rev> <manifest-filename>. Prints the version or "-".
version_at_rev() {
    local rev="$1" manifest="$2"
    if [ "$manifest" = "package.json" ]; then
        git show "$rev:$manifest" 2>/dev/null \
            | python3 -c 'import sys,json; print(json.load(sys.stdin).get("version") or "-")' 2>/dev/null || echo "-"
    else
        git show "$rev:$manifest" 2>/dev/null \
            | python3 -c 'import sys,tomllib; p=tomllib.load(sys.stdin.buffer).get("project",{}); print(p.get("version") or "-")' 2>/dev/null || echo "-"
    fi
}

# Dry-run report for one package: mirror exactly what `git subtree pull` would do
# without applying it. The boundary is git's own subtree bookmark — the most
# recent `Squashed '<prefix>/' changes from <old>..<new>` commit pins <new> as the
# last remote SHA vendored here. The commits in <new>..remote-master are precisely
# what a real sync would import. Also reports the version delta and FLAGS the
# release-risk case: commits are pending but the version was NOT bumped.
dry_run_package() {
    local pkg="$1" remote_url="$2"
    local prefix="$PARENT_DIR/$pkg"

    # Vendored version + which manifest carries it.
    local vendored_version manifest
    read -r vendored_version manifest < <(vendored_manifest_version "$prefix")

    # Fetch the remote branch into FETCH_HEAD without touching the working tree.
    if ! git fetch --quiet "$remote_url" "$BRANCH" 2>/dev/null; then
        echo "  ⚠️  could not fetch $remote_url ($BRANCH) — skipping"
        echo ""
        return
    fi
    local remote_tip remote_version
    remote_tip="$(git rev-parse FETCH_HEAD)"
    remote_version="$(version_at_rev FETCH_HEAD "$manifest")"

    echo "📦 $pkg  (framework=$vendored_version  remote=$remote_version)  [$manifest]"

    # Authoritative boundary: the last-synced remote SHA from the subtree bookmark.
    local last_synced
    last_synced="$(git log --all --grep="Squashed '$prefix/' changes from" --format="%s" \
        | head -1 | sed -nE "s/.*\.\.([0-9a-f]+).*/\1/p")"

    if [ -z "$last_synced" ] || ! git cat-file -e "${last_synced}^{commit}" 2>/dev/null; then
        echo "  ⚠️  no subtree-sync bookmark found (or its SHA is unreachable);"
        echo "      cannot pin what was last synced. Last 10 remote commits:"
        git log -10 --format='    %h %s' "$remote_tip"
        echo ""
        return
    fi

    local ahead
    ahead="$(git rev-list --count "${last_synced}..${remote_tip}")"
    if [ "$ahead" -eq 0 ]; then
        echo "  ✓ up to date — nothing to sync"
        echo ""
        return
    fi

    echo "  ⬆ $ahead commit(s) would be synced (framework behind since ${last_synced:0:9}):"
    git log --format='    %h %s' "${last_synced}..${remote_tip}"

    # Release-risk flag: pending commits but the version did not move. A sync would
    # overwrite the vendored package with NO version bump to signal the change.
    if [ "$remote_version" = "$vendored_version" ] && [ "$vendored_version" != "-" ]; then
        echo "  ⚠️  VERSION NOT BUMPED — remote has $ahead new commit(s) but $manifest is still"
        echo "      $remote_version (same as framework). Bump the version before releasing."
    fi
    echo ""
}

if [ "$DRY_RUN" -eq 1 ]; then
    echo "--- DRY RUN: comparing vendored packages against remote '$BRANCH' (no changes made) ---"
    echo ""
    for name in "${PACKAGES[@]}"; do
        PKG="akgentic-$name"
        if [ ! -d "$PARENT_DIR/$PKG" ]; then
            echo "⚠️  Skipping $PKG: directory $PARENT_DIR/$PKG does not exist." >&2
            continue
        fi
        dry_run_package "$PKG" "git@github.com:$ORG/$PKG.git"
    done
    echo "--- Dry run complete. Re-run without --dry-run to apply. ---"
    exit 0
fi

if [ -n "$(git status --porcelain)" ]; then
    echo "❌ Working tree has uncommitted changes. Commit or stash them before syncing." >&2
    git status --short >&2
    exit 1
fi

echo "--- Syncing packages on $BRANCH ---"

tag_failures=()

for name in "${PACKAGES[@]}"; do
    PKG="akgentic-$name"
    if [ ! -d "$PARENT_DIR/$PKG" ]; then
        echo "⚠️  Skipping $PKG: directory $PARENT_DIR/$PKG does not exist." >&2
        continue
    fi
    REMOTE_URL="git@github.com:$ORG/$PKG.git"

    echo "🔄 Updating $PKG from $REMOTE_URL..."
    git subtree pull --prefix "$PARENT_DIR/$PKG" "$REMOTE_URL" "$BRANCH" --squash -m "chore(release): sync $PKG from $BRANCH"

    if [ "$NO_TAG" -eq 0 ]; then
        # Freeze the same version on the package's own repo (vX.Y.Z at remote HEAD).
        if ! freeze_package_tag "$PKG" "$REMOTE_URL"; then
            tag_failures+=("$PKG")
        fi
    fi
done

if [ "${#tag_failures[@]}" -gt 0 ]; then
    echo "--- Sync done, but version tagging FAILED for: ${tag_failures[*]} ---" >&2
    echo "    Resolve the version collisions above (bump pyproject.toml) and re-run." >&2
    exit 1
fi

echo "--- Sync complete. Packages updated and versions frozen with vX.Y.Z tags. ---"