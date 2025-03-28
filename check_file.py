#!/usr/bin/env python
# -*- coding: utf-8 -*-
import os
import sys

def main():
    """检查文件存在性和基本信息"""
    if len(sys.argv) < 2:
        print("用法: python check_file.py <文件路径>")
        return 1
        
    file_path = sys.argv[1]
    abs_path = os.path.abspath(file_path)
    
    print(f"检查文件: {file_path}")
    print(f"绝对路径: {abs_path}")
    
    if os.path.exists(file_path):
        print(f"✅ 文件存在")
        size = os.path.getsize(file_path)
        print(f"文件大小: {size} 字节 ({size/1024/1024:.2f} MB)")
        
        # 尝试读取文件的前100个字节
        try:
            with open(file_path, 'rb') as f:
                data = f.read(100)
                print(f"成功读取文件前100个字节: {data.hex()[:30]}...")
        except Exception as e:
            print(f"❌ 读取文件失败: {str(e)}")
    else:
        print(f"❌ 文件不存在")
        
        # 检查父目录
        parent_dir = os.path.dirname(file_path)
        if os.path.exists(parent_dir):
            print(f"✅ 父目录存在: {parent_dir}")
            
            # 列出父目录中的文件
            print(f"父目录中的文件:")
            for item in os.listdir(parent_dir):
                item_path = os.path.join(parent_dir, item)
                if os.path.isfile(item_path):
                    item_size = os.path.getsize(item_path)
                    print(f"  - {item} ({item_size/1024/1024:.2f} MB)")
        else:
            print(f"❌ 父目录不存在: {parent_dir}")
            
    return 0

if __name__ == "__main__":
    sys.exit(main()) 