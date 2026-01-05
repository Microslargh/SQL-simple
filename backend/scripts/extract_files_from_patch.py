#!/usr/bin/env python3
"""
从patch文件中提取文件列表和目录结构
用法: python extract_files_from_patch.py <patch_file> [--output-dir <dir>] [--create-dirs]
"""
import re
import os
import sys
import argparse
from pathlib import Path
from typing import Set, List, Tuple


def extract_files_from_patch(patch_file: str) -> Tuple[Set[str], Set[str]]:
    """
    从patch文件中提取所有涉及的文件路径
    
    Args:
        patch_file: patch文件路径
    
    Returns:
        Tuple[新增文件集合, 修改文件集合]
    """
    added_files = set()
    modified_files = set()
    
    with open(patch_file, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()
    
    # 匹配文件路径模式
    # diff --git a/path/to/file b/path/to/file
    # new file mode
    # deleted file mode
    file_pattern = r'^diff --git a/(.+?) b/(.+?)$'
    new_file_pattern = r'^new file mode'
    deleted_file_pattern = r'^deleted file mode'
    
    lines = content.split('\n')
    i = 0
    while i < len(lines):
        line = lines[i]
        
        # 匹配 diff --git 行
        match = re.match(file_pattern, line)
        if match:
            old_path = match.group(1)
            new_path = match.group(2)
            
            # 检查下一行是否是 new file 或 deleted file
            if i + 1 < len(lines):
                next_line = lines[i + 1]
                if re.match(new_file_pattern, next_line):
                    added_files.add(new_path)
                elif re.match(deleted_file_pattern, next_line):
                    # 删除的文件也记录
                    modified_files.add(old_path)
                else:
                    # 修改的文件
                    if old_path == new_path:
                        modified_files.add(new_path)
                    else:
                        # 重命名的文件
                        modified_files.add(old_path)
                        added_files.add(new_path)
        
        i += 1
    
    return added_files, modified_files


def get_all_files_from_patch(patch_file: str) -> Set[str]:
    """
    从patch文件中提取所有涉及的文件路径（包括新增和修改）
    
    Args:
        patch_file: patch文件路径
    
    Returns:
        所有文件路径的集合
    """
    added_files, modified_files = extract_files_from_patch(patch_file)
    return added_files | modified_files


def create_directory_structure(files: Set[str], output_dir: str = None, create_dirs: bool = False):
    """
    根据文件列表创建目录结构
    
    Args:
        files: 文件路径集合
        output_dir: 输出目录（如果为None，则只打印）
        create_dirs: 是否实际创建目录
    """
    directories = set()
    
    for file_path in sorted(files):
        # 获取目录路径
        dir_path = os.path.dirname(file_path)
        if dir_path:
            directories.add(dir_path)
        
        # 如果是创建目录模式，创建目录
        if create_dirs and output_dir:
            full_dir_path = os.path.join(output_dir, dir_path) if dir_path else output_dir
            os.makedirs(full_dir_path, exist_ok=True)
            print(f"Created directory: {full_dir_path}")
    
    return directories


def generate_file_tree(files: Set[str], directories: Set[str]) -> str:
    """
    生成文件树结构（文本格式）
    
    Args:
        files: 文件路径集合
        directories: 目录路径集合
    
    Returns:
        文件树字符串
    """
    tree_lines = []
    
    # 按层级组织目录和文件
    all_paths = sorted(files | directories)
    
    # 构建树结构
    tree = {}
    for path in all_paths:
        parts = path.split('/')
        current = tree
        for part in parts:
            if part not in current:
                current[part] = {}
            current = current[part]
    
    def print_tree(node, prefix="", is_last=True):
        """递归打印树结构"""
        items = sorted(node.items())
        for i, (name, children) in enumerate(items):
            is_last_item = i == len(items) - 1
            current_prefix = "└── " if is_last_item else "├── "
            tree_lines.append(f"{prefix}{current_prefix}{name}")
            
            if children:
                extension = "    " if is_last_item else "│   "
                print_tree(children, prefix + extension, is_last_item)
    
    print_tree(tree)
    return "\n".join(tree_lines)


def main():
    parser = argparse.ArgumentParser(
        description='从patch文件中提取文件列表和目录结构',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 只列出文件
  python extract_files_from_patch.py 主要提交.patch
  
  # 生成文件树
  python extract_files_from_patch.py 主要提交.patch --tree
  
  # 创建目录结构
  python extract_files_from_patch.py 主要提交.patch --output-dir ./extracted --create-dirs
  
  # 保存文件列表到文件
  python extract_files_from_patch.py 主要提交.patch --output-file files.txt
        """
    )
    
    parser.add_argument('patch_file', help='patch文件路径')
    parser.add_argument('--output-dir', '-o', help='输出目录（用于创建目录结构）')
    parser.add_argument('--create-dirs', '-c', action='store_true', help='实际创建目录结构')
    parser.add_argument('--output-file', '-f', help='将文件列表保存到文件')
    parser.add_argument('--tree', '-t', action='store_true', help='生成文件树结构')
    parser.add_argument('--separate', '-s', action='store_true', help='分别显示新增和修改的文件')
    
    args = parser.parse_args()
    
    if not os.path.exists(args.patch_file):
        print(f"错误: patch文件不存在: {args.patch_file}", file=sys.stderr)
        sys.exit(1)
    
    # 提取文件
    if args.separate:
        added_files, modified_files = extract_files_from_patch(args.patch_file)
        all_files = added_files | modified_files
        
        print("=" * 80)
        print("新增文件:")
        print("=" * 80)
        for file_path in sorted(added_files):
            print(f"  + {file_path}")
        
        print("\n" + "=" * 80)
        print("修改文件:")
        print("=" * 80)
        for file_path in sorted(modified_files):
            print(f"  M {file_path}")
        
        print("\n" + "=" * 80)
        print(f"总计: 新增 {len(added_files)} 个文件, 修改 {len(modified_files)} 个文件")
    else:
        all_files = get_all_files_from_patch(args.patch_file)
        
        print("=" * 80)
        print("涉及的文件列表:")
        print("=" * 80)
        for file_path in sorted(all_files):
            print(f"  {file_path}")
        print(f"\n总计: {len(all_files)} 个文件")
    
    # 生成文件树
    if args.tree:
        directories = create_directory_structure(all_files)
        print("\n" + "=" * 80)
        print("目录结构:")
        print("=" * 80)
        tree_str = generate_file_tree(all_files, directories)
        print(tree_str)
    
    # 创建目录结构
    if args.create_dirs and args.output_dir:
        print("\n" + "=" * 80)
        print("创建目录结构:")
        print("=" * 80)
        create_directory_structure(all_files, args.output_dir, create_dirs=True)
    
    # 保存到文件
    if args.output_file:
        with open(args.output_file, 'w', encoding='utf-8') as f:
            for file_path in sorted(all_files):
                f.write(f"{file_path}\n")
        print(f"\n文件列表已保存到: {args.output_file}")
    
    # 生成目录结构文件
    if args.output_dir:
        directories = create_directory_structure(all_files)
        dir_file = os.path.join(args.output_dir, 'directories.txt')
        os.makedirs(args.output_dir, exist_ok=True)
        with open(dir_file, 'w', encoding='utf-8') as f:
            for dir_path in sorted(directories):
                f.write(f"{dir_path}\n")
        print(f"目录列表已保存到: {dir_file}")


if __name__ == '__main__':
    main()

