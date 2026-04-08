"""Test script for skills API endpoints."""

import asyncio
from pathlib import Path

from api.skills_api import _build_file_tree, _validate_path


async def test_file_tree():
    """Test file tree building."""
    skills_dir = Path("skills")
    skill_name = "skill-creator"
    skill_dir = skills_dir / skill_name

    if not skill_dir.exists():
        print("❌ skill-creator directory not found")
        return

    print(f"✅ Testing file tree for {skill_name}")
    tree = _build_file_tree(skill_dir, skill_dir)
    print(f"✅ Found {len(tree)} top-level items")

    for node in tree[:5]:  # Show first 5 items
        print(f"  - {node.path} ({node.type})")


async def test_path_validation():
    """Test path validation."""
    skills_dir = Path("skills")
    skill_name = "skill-creator"

    # Valid path
    try:
        target = _validate_path(skills_dir, skill_name, "SKILL.md")
        print(f"✅ Valid path: {target}")
    except Exception as e:
        print(f"❌ Valid path failed: {e}")

    # Invalid path (traversal attempt)
    try:
        target = _validate_path(skills_dir, skill_name, "../../../etc/passwd")
        print(f"❌ Path traversal not blocked: {target}")
    except Exception as e:
        print(f"✅ Path traversal blocked: {e}")


if __name__ == "__main__":
    asyncio.run(test_file_tree())
    asyncio.run(test_path_validation())
