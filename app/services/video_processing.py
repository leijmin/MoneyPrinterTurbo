import os
import json
import subprocess
from loguru import logger
from app.services.video_metadata import VideoMetadataExtractor, VideoDetailedMetadata

class VideoProcessor:
    """视频处理基类，提供统一的视频处理流程和特性检测"""
    
    @staticmethod
    def get_video_features(video_path):
        """获取视频特性 - 使用统一的VideoMetadataExtractor，增强MOV格式支持"""
        try:
            # 检测是否为MOV格式
            is_mov_format = video_path.lower().endswith('.mov')
            
            # 使用元数据提取器获取元数据对象
            metadata = VideoMetadataExtractor.get_video_metadata(video_path)
            
            # 如果已经是VideoDetailedMetadata对象，直接使用to_features()方法
            if hasattr(metadata, 'to_features'):
                features = metadata.to_features()
                
                # MOV格式特殊处理
                if is_mov_format:
                    # 确保MOV格式的旋转状态正确
                    needs_rotation = metadata.rotation != 0
                    features["needs_rotation"] = needs_rotation
                    
                    # 根据旋转角度重新计算有效宽高
                    if needs_rotation and metadata.rotation in [90, 270]:
                        features["effective_width"] = metadata.height
                        features["effective_height"] = metadata.width
                    
                    logger.info(f"MOV格式视频特性补充: 旋转={metadata.rotation}°, 需要旋转={needs_rotation}")
                
                return features
            
            # 向后兼容...（现有代码）
        except Exception as e:
            logger.error(f"获取视频特性时出错: {str(e)}")
            # 记录错误堆栈
            import traceback
            logger.debug(f"错误堆栈: {traceback.format_exc()}")
            return None
    
    @staticmethod
    def optimize_encoding_params(features, base_params):
        """根据视频特性优化编码参数"""
        params = base_params.copy()
        
        # 高分辨率视频处理逻辑
        if features["is_4k"]:
            # 对所有4K视频提高码率，无论编码类型
            params["bitrate"] = min(20000, int(params["bitrate"] * 1.5))
            params["maxrate"] = min(30000, int(params["maxrate"] * 1.5))
            params["bufsize"] = min(40000, int(params["bufsize"] * 1.5))
            logger.info(f"检测到4K视频，提高编码质量参数")
        
        # 处理旋转高清视频
        if features["needs_rotation"] and features["is_high_quality"]:
            # 对需要旋转的高清视频进行特殊处理
            if not features["is_4k"]:  # 避免与4K处理重复
                params["bitrate"] = int(params["bitrate"] * 1.1)
            
            # 对某些编码器增加关键帧频率，确保旋转后画面清晰
            params["g"] = "50"  # 设置GOP大小
            logger.info("针对旋转的高清视频优化编码参数")
        
        return params
    
    @staticmethod
    def build_filter_string(features, target_width, target_height):
        """构建统一的滤镜字符串"""
        filters = []
        
        # 1. 处理旋转 - 对所有需要旋转的视频统一处理，不区分编码格式
        if features["needs_rotation"]:
            rotation = features["rotation"]
            if rotation == 90:
                filters.append("transpose=1")  # 顺时针90度
            elif rotation == 180:
                filters.append("transpose=2,transpose=2")  # 180度
            elif rotation == 270 or rotation == -90:
                filters.append("transpose=2")  # 逆时针90度
        
        # 2. 缩放和填充处理 - 确保所有视频获得相同的处理
        width = features["width"]
        height = features["height"]
        effective_width = features["effective_width"]
        effective_height = features["effective_height"]
        
        # 所有视频统一使用相同的缩放和填充逻辑
        # 检查是否需要缩放
        if effective_width > target_width or effective_height > target_height:
            # 计算缩放比例
            scale_ratio = min(target_width / effective_width, target_height / effective_height)
            scaled_width = int(effective_width * scale_ratio)
            scaled_height = int(effective_height * scale_ratio)
            
            # 缩放滤镜 - 使用更高质量的lanczos算法
            scale_filter = f"scale={scaled_width}:{scaled_height}:flags=lanczos"
            filters.append(scale_filter)
            
            # 任何视频在缩放后如果尺寸小于目标尺寸，都添加黑边填充
            pad_filter = f"pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2:color=black"
            filters.append(pad_filter)
        
        # 3. 确保yuv420p像素格式
        filters.append("format=yuv420p")
        
        # 组合所有滤镜
        return ",".join(filters) if filters else "null" 