"""Pinned custom-node checkouts should ignore untracked worktree files."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_pinned_custom_node_dirty_check_ignores_untracked_files():
    lines = (ROOT / "install.sh").read_text(encoding="utf-8").splitlines()
    matched = [line.strip() for line in lines if 'git -C "$dest" status --porcelain' in line]
    assert matched == [
        'if [ -n "$(git -C "$dest" status --porcelain --untracked-files=no)" ]; then'
    ]
