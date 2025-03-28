#!/usr/bin/env python
import os
import sys

# 添加项目根目录到路径
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from app.services.video_metadata import VideoMetadataExtractor

def main():
    video_path = r"G:\MoneyPrinterTurbo\storage\local_videos\整机展示.mp4"
    
    if not os.path.exists(video_path):
        print(f"视频文件不存在: {video_path}")
        return
    
    print(f"开始分析视频: {video_path}")
    
    # 获取视频元数据
    metadata = VideoMetadataExtractor.get_video_metadata(video_path)
    print("视频元数据:")
    for k, v in metadata.items():
        print(f"  {k}: {v}")

if __name__ == "__main__":
    main() 