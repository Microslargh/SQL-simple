#!/usr/bin/env python3
"""
根据patch文件生成对应的文件结构（从本地项目复制完整文件）
用法: python generate_files_from_patch.py <patch_file> [--output-dir <dir>] [--source-dir <dir>]
"""
import re
import os
import sys
import shutil
import argparse
from pathlib import Path
from typing import Set, Dict, List


def parse_patch_file(patch_file: str) -> Dict[str, Dict]:
    """
    解析patch文件，提取文件信息和内容
    支持标准git patch格式和IntelliJ IDEA patch格式
    
    Args:
        patch_file: patch文件路径
    
    Returns:
        文件信息字典: {file_path: {type: 'new'|'modified', content: str}}
    """
    files_info = {}
    
    with open(patch_file, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()
    
    # 支持两种格式：
    # 1. 标准git格式: diff --git a/path b/path
    # 2. IntelliJ格式: Index: path
    
    # 检查是否是IntelliJ格式（包含Index:行）
    has_index_format = re.search(r'^Index:\s+', content, re.MULTILINE)
    
    # 先尝试标准git格式
    file_patches = re.split(r'^diff --git', content, flags=re.MULTILINE)
    
    if len(file_patches) > 1 and not has_index_format:
        # 标准git格式
        for patch in file_patches:
            if not patch.strip():
                continue
            
            # 提取文件路径
            file_match = re.search(r'^a/(.+?)\s+b/(.+?)$', patch, re.MULTILINE)
            if not file_match:
                continue
            
            old_path = file_match.group(1)
            new_path = file_match.group(2)
            
            # 判断文件类型
            is_new_file = 'new file mode' in patch
            is_deleted = 'deleted file mode' in patch
            
            # 提取文件内容
            content_lines = []
            in_content = False
            for line in patch.split('\n'):
                if line.startswith('+++'):
                    in_content = True
                    continue
                if in_content and line.startswith('+') and not line.startswith('+++'):
                    # 移除开头的 +
                    content_lines.append(line[1:])
                elif in_content and line.startswith('@@'):
                    continue
                elif in_content and not line.startswith(('+', '-', '@', '\\')):
                    if line.strip() or content_lines:
                        content_lines.append(line)
            
            file_content = '\n'.join(content_lines)
            
            if is_new_file:
                files_info[new_path] = {
                    'type': 'new',
                    'content': file_content,
                    'old_path': None
                }
            elif is_deleted:
                files_info[old_path] = {
                    'type': 'deleted',
                    'content': None,
                    'old_path': None
                }
            else:
                files_info[new_path] = {
                    'type': 'modified',
                    'content': file_content,
                    'old_path': old_path if old_path != new_path else None
                }
    else:
        # IntelliJ IDEA格式: Index: path
        # 使用正则表达式提取所有Index行
        index_pattern = r'^Index:\s*(.+?)$'
        matches = list(re.finditer(index_pattern, content, re.MULTILINE))
        
        for i, match in enumerate(matches):
            file_path = match.group(1).strip()
            
            # 查找对应的diff块（从当前Index到下一个Index或文件结尾）
            start_pos = match.end()
            if i + 1 < len(matches):
                next_match = matches[i + 1]
                end_pos = next_match.start()
            else:
                end_pos = len(content)
            
            patch_block = content[start_pos:end_pos]
            
            # 判断文件类型
            is_new_file = '/dev/null' in patch_block or 'new file mode' in patch_block
            is_deleted = 'deleted file mode' in patch_block
            
            # 提取文件路径（从diff --git行）
            diff_match = re.search(r'diff --git a/(.+?)\s+b/(.+?)$', patch_block, re.MULTILINE)
            if diff_match:
                old_path = diff_match.group(1)
                new_path = diff_match.group(2)
                if file_path != new_path:
                    file_path = new_path  # 使用diff中的路径
            
            # 提取文件内容
            content_lines = []
            in_hunk = False
            for line in patch_block.split('\n'):
                # 跳过元数据行
                if line.startswith(('Index:', 'IDEA', 'Subsystem', 'CharsetEP', '=', '---', '+++')):
                    continue
                # 进入hunk（@@行）
                if line.startswith('@@'):
                    in_hunk = True
                    continue
                # 提取内容行
                if in_hunk:
                    if line.startswith('+') and not line.startswith('+++'):
                        content_lines.append(line[1:])
                    elif line.startswith(' ') or (line == '' and content_lines):
                        # 上下文行或空行（如果有内容）
                        if content_lines:  # 只在有新增内容时保留上下文
                            content_lines.append(line[1:] if line.startswith(' ') else line)
            
            file_content = '\n'.join(content_lines)
            
            if is_new_file:
                files_info[file_path] = {
                    'type': 'new',
                    'content': file_content,
                    'old_path': None
                }
            elif is_deleted:
                files_info[file_path] = {
                    'type': 'deleted',
                    'content': None,
                    'old_path': None
                }
            else:
                files_info[file_path] = {
                    'type': 'modified',
                    'content': file_content,
                    'old_path': None
                }
    
    return files_info


def copy_files_from_source(files_info: Dict[str, Dict], source_dir: str, output_dir: str):
    """
    从本地项目目录复制完整文件到输出目录
    
    Args:
        files_info: 文件信息字典
        source_dir: 源项目目录（通常是项目根目录）
        output_dir: 输出目录
    """
    created_dirs = set()
    copied_files = []
    missing_files = []
    
    for file_path, info in sorted(files_info.items()):
        if info['type'] == 'deleted':
            print(f"[DELETED] {file_path}")
            continue
        
        # 源文件路径
        source_file_path = os.path.join(source_dir, file_path)
        
        # 目标文件路径
        target_file_path = os.path.join(output_dir, file_path)
        
        # 创建目录
        dir_path = os.path.dirname(file_path)
        if dir_path:
            full_dir_path = os.path.join(output_dir, dir_path)
            if full_dir_path not in created_dirs:
                os.makedirs(full_dir_path, exist_ok=True)
                created_dirs.add(full_dir_path)
                print(f"[DIR]     {dir_path}/")
        
        # 检查源文件是否存在
        if os.path.exists(source_file_path):
            try:
                # 复制文件
                shutil.copy2(source_file_path, target_file_path)
                file_size = os.path.getsize(target_file_path)
                print(f"[COPY]    {file_path} ({file_size} bytes)")
                copied_files.append(file_path)
            except Exception as e:
                print(f"[ERROR]   复制失败 {file_path}: {str(e)}")
                missing_files.append(file_path)
        else:
            print(f"[MISSING] {file_path} (源文件不存在)")
            missing_files.append(file_path)
            
            # 如果源文件不存在，创建占位符
            try:
                with open(target_file_path, 'w', encoding='utf-8') as f:
                    f.write(f"# File: {file_path}\n")
                    f.write(f"# Type: {info['type']}\n")
                    f.write(f"# Source file not found: {source_file_path}\n")
                    if info['content']:
                        f.write(f"\n# Content from patch:\n")
                        f.write(info['content'])
            except Exception as e:
                print(f"[ERROR]   创建占位符失败 {file_path}: {str(e)}")
    
    return created_dirs, copied_files, missing_files


def generate_summary(files_info: Dict[str, Dict], output_file: str = None):
    """
    生成文件摘要报告
    
    Args:
        files_info: 文件信息字典
        output_file: 输出文件路径（可选）
    """
    new_files = [f for f, info in files_info.items() if info['type'] == 'new']
    modified_files = [f for f, info in files_info.items() if info['type'] == 'modified']
    deleted_files = [f for f, info in files_info.items() if info['type'] == 'deleted']
    
    summary = []
    summary.append("=" * 80)
    summary.append("文件变更摘要")
    summary.append("=" * 80)
    summary.append(f"\n新增文件 ({len(new_files)}):")
    for f in sorted(new_files):
        summary.append(f"  + {f}")
    
    summary.append(f"\n修改文件 ({len(modified_files)}):")
    for f in sorted(modified_files):
        summary.append(f"  M {f}")
    
    if deleted_files:
        summary.append(f"\n删除文件 ({len(deleted_files)}):")
        for f in sorted(deleted_files):
            summary.append(f"  - {f}")
    
    summary.append("\n" + "=" * 80)
    summary.append(f"总计: {len(files_info)} 个文件")
    summary.append("=" * 80)
    
    summary_text = "\n".join(summary)
    print(summary_text)
    
    if output_file:
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(summary_text)
        print(f"\n摘要已保存到: {output_file}")


def main():
    parser = argparse.ArgumentParser(
        description='根据patch文件从本地项目复制完整文件到目标目录',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 只显示文件列表
  python generate_files_from_patch.py 主要提交.patch
  
  # 从本地项目复制文件到目标目录
  python generate_files_from_patch.py 主要提交.patch --output-dir ./extracted --source-dir .
  
  # 指定源目录（项目根目录）
  python generate_files_from_patch.py 主要提交.patch --output-dir ./extracted --source-dir /path/to/project
  
  # 生成摘要报告
  python generate_files_from_patch.py 主要提交.patch --summary summary.txt --list-only
        """
    )
    
    parser.add_argument('patch_file', help='patch文件路径')
    parser.add_argument('--output-dir', '-o', help='输出目录（必需，如果要从源目录复制文件）')
    parser.add_argument('--source-dir', '-s', default='.', help='源项目目录（默认：当前目录）')
    parser.add_argument('--summary', help='生成摘要报告文件')
    parser.add_argument('--list-only', '-l', action='store_true', help='只列出文件，不复制任何内容')
    
    args = parser.parse_args()
    
    if not os.path.exists(args.patch_file):
        print(f"错误: patch文件不存在: {args.patch_file}", file=sys.stderr)
        sys.exit(1)
    
    # 解析patch文件
    print(f"正在解析patch文件: {args.patch_file}")
    files_info = parse_patch_file(args.patch_file)
    print(f"找到 {len(files_info)} 个文件\n")
    
    # 生成摘要
    generate_summary(files_info, args.summary)
    
    # 如果只是列出文件，就退出
    if args.list_only:
        return
    
    # 从源目录复制文件
    if args.output_dir:
        source_dir = os.path.abspath(args.source_dir)
        output_dir = os.path.abspath(args.output_dir)
        
        if not os.path.exists(source_dir):
            print(f"错误: 源目录不存在: {source_dir}", file=sys.stderr)
            sys.exit(1)
        
        print(f"\n正在从源目录复制文件:")
        print(f"  源目录: {source_dir}")
        print(f"  目标目录: {output_dir}")
        print("-" * 80)
        
        created_dirs, copied_files, missing_files = copy_files_from_source(
            files_info,
            source_dir,
            output_dir
        )
        
        print("-" * 80)
        print(f"\n完成!")
        print(f"  创建目录: {len(created_dirs)} 个")
        print(f"  复制文件: {len(copied_files)} 个")
        if missing_files:
            print(f"  缺失文件: {len(missing_files)} 个")
            print("\n缺失的文件列表:")
            for f in missing_files:
                print(f"    - {f}")
    else:
        print("\n提示: 使用 --output-dir 参数指定输出目录来复制文件")
        print("     使用 --source-dir 参数指定源项目目录（默认：当前目录）")


if __name__ == '__main__':
    main()

