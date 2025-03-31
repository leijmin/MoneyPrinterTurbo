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
            # is_mov_format = video_path.lower().endswith('.mov')
            
            # 使用元数据提取器获取元数据对象
            metadata = VideoMetadataExtractor.get_video_metadata(video_path)
            
            # 如果已经是VideoDetailedMetadata对象，直接使用to_features()方法
            if hasattr(metadata, 'to_features'):
                features = metadata.to_features()
                
                # MOV格式特殊处理
                # if is_mov_format:
                #     # 确保MOV格式的旋转状态正确
                #     needs_rotation = metadata.rotation != 0
                #     features["needs_rotation"] = needs_rotation
                    
                #     # 根据旋转角度重新计算有效宽高
                #     if needs_rotation and metadata.rotation in [90, 270]:
                #         features["effective_width"] = metadata.height
                #         features["effective_height"] = metadata.width
                    
                #     logger.info(f"MOV格式视频特性补充: 旋转={metadata.rotation}°, 需要旋转={needs_rotation}")
                
                return features
            
            # 向后兼容...（现有代码）
        except Exception as e:
            logger.error(f"获取视频特性时出错: {str(e)}")
            # 记录错误堆栈
            import traceback
            logger.debug(f"错误堆栈: {traceback.format_exc()}")
            return None
    
    @staticmethod
    def optimize_encoding_params(metadata, base_params):
        """根据视频特性优化编码参数"""
        params = base_params.copy()
        
        # 高分辨率视频处理逻辑
        if metadata["is_4k"]:
            # 对所有4K视频提高码率，无论编码类型
            params["bitrate"] = min(20000, int(params["bitrate"] * 1.5))
            params["maxrate"] = min(30000, int(params["maxrate"] * 1.5))
            params["bufsize"] = min(40000, int(params["bufsize"] * 1.5))
            logger.info(f"检测到4K视频，提高编码质量参数")
        
        # 处理旋转高清视频
        if metadata["needs_rotation"] and metadata["is_high_quality"]:
            # 对需要旋转的高清视频进行特殊处理
            if not metadata["is_4k"]:  # 避免与4K处理重复
                params["bitrate"] = int(params["bitrate"] * 1.1)
            
            # 对某些编码器增加关键帧频率，确保旋转后画面清晰
            params["g"] = "50"  # 设置GOP大小
            logger.info("针对旋转的高清视频优化编码参数")
        
        return params
    
    @staticmethod
    def build_filter_string(features, target_width, target_height):
        """构建统一的滤镜字符串 - 与预处理阶段保持一致"""
        filters = []
        
        # 检查文件名是否表明视频已经处理过旋转
        rotation_handled = features.get("rotation_handled", False)
        is_preprocessed = features.get("is_preprocessed", False)
        filename = features.get("filename", "")
        
        # 更严格地检查是否已经处理过旋转
        if (is_preprocessed or 
            rotation_handled or 
            (filename and ("_processed" in filename or "proc_" in filename))):
            rotation_handled = True
            logger.info(f"跳过旋转处理，视频已预处理: {filename}")
        
        # 1. 处理旋转 - 如果还未处理过旋转，且需要旋转
        if features.get("needs_rotation", False) and not rotation_handled:
            rotation = features["rotation"]
            if rotation == 90:
                filters.append("transpose=2")  # 90度旋转 (需顺时针270度校正) -> transpose=2
                logger.info(f"应用顺时针90度旋转滤镜")
            elif rotation == 180:
                filters.append("hflip,vflip")  # 180度旋转 -> 垂直和水平翻转
                logger.info(f"应用180度旋转滤镜")
            elif rotation == 270 or rotation == -90:
                filters.append("transpose=0")  # 270度旋转 (需顺时针90度校正) -> transpose=0
                logger.info(f"应用逆时针90度旋转滤镜")
            
            # 添加抗锯齿处理
            filters.append("smartblur=3:0.8:0")
            logger.info("应用smartblur滤镜防止旋转引起的锯齿")
        
        # 如果已预处理，跳过缩放和填充
        if is_preprocessed:
            logger.info("跳过缩放和填充，视频已预处理")
            # 确保yuv420p像素格式
            if filters:
                filters.append("format=yuv420p")
            return ",".join(filters) if filters else "null"
        
        # 2. 缩放和填充处理
        width = features["width"]
        height = features["height"]
        effective_width = features["effective_width"]
        effective_height = features["effective_height"]
        
        # 检查是否需要缩放和填充
        if effective_width != target_width or effective_height != target_height:
            # 计算缩放比例，保持宽高比
            scale_ratio = min(target_width / effective_width, target_height / effective_height)
            scaled_width = int(effective_width * scale_ratio)
            scaled_height = int(effective_height * scale_ratio)
            
            # 高质量缩放处理 - 使用lanczos算法
            scale_filter = f"scale={scaled_width}:{scaled_height}:flags=lanczos"
            filters.append(scale_filter)
            
            # 如果缩放后的尺寸小于目标尺寸，添加填充
            if scaled_width != target_width or scaled_height != target_height:
                pad_filter = f"pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2:color=black"
                filters.append(pad_filter)
                logger.info(f"应用缩放与填充: 缩放至{scaled_width}x{scaled_height}并填充至{target_width}x{target_height}")
            else:
                logger.info(f"应用精确缩放: {effective_width}x{effective_height} -> {target_width}x{target_height}")
        
        # 3. 确保yuv420p像素格式
        filters.append("format=yuv420p")
        
        # 组合所有滤镜
        filter_string = ",".join(filters) if filters else "null"
        logger.info(f"最终滤镜串: {filter_string}")
        return filter_string 