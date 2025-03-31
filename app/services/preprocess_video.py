import os
import json
import subprocess
import re
from typing import List, Tuple, Optional
from loguru import logger

from app.models import const
from app.models.schema import MaterialInfo, VideoAspect
from app.utils import utils
from app.services.video_metadata import VideoMetadataExtractor
from app.services.video_encoder import HardwareAccelerator , EncoderConfig
from app.models.schema import VideoAspect


class VideoPreprocessor:
    """
    视频预处理类，专门处理视频素材和图片转视频的功能
    """
    
    @staticmethod
    def get_optimal_scale_mode(v_width: int, v_height: int, 
                              target_width: int, target_height: int) -> Tuple[str, float]:
        """
        根据源视频和目标尺寸，选择最佳的缩放模式，增强版本
        
        Args:
            v_width: 源视频宽度
            v_height: 源视频高度
            target_width: 目标宽度
            target_height: 目标高度
            
        Returns:
            (缩放滤镜, 缩放比例)
        """
        try:
            # 计算宽高比
            src_ratio = v_width / v_height
            target_ratio = target_width / target_height
            
            # 检查源尺寸是否小于目标尺寸，这种情况下不能使用裁剪，只能使用填充
            if v_width < target_width or v_height < target_height:
                logger.info(f"源视频尺寸({v_width}x{v_height})小于目标尺寸({target_width}x{target_height})，使用填充模式")
                # 使用填充模式，先缩放保持比例，然后填充黑边
                # 计算等比例缩放的尺寸
                scale_ratio = min(target_width / v_width, target_height / v_height)
                scaled_width = int(v_width * scale_ratio)
                scaled_height = int(v_height * scale_ratio)
                
                # 计算填充位置（居中）
                pad_x = (target_width - scaled_width) // 2
                pad_y = (target_height - scaled_height) // 2
                
                # 构建滤镜：缩放后填充
                scale_filter = f"scale={scaled_width}:{scaled_height}:force_original_aspect_ratio=1"
                pad_filter = f",pad={target_width}:{target_height}:{pad_x}:{pad_y}:color=black"
                
                return scale_filter + pad_filter, scale_ratio
            
            # 标准处理逻辑（源尺寸大于目标尺寸的情况）
            if src_ratio > target_ratio:
                # 源视频更宽，需要按高度缩放并裁剪宽度
                scale_h = target_height
                scale_w = int(v_width * (target_height / v_height))
                scale_filter = f"scale={scale_w}:{scale_h}:force_original_aspect_ratio=1"
                
                # 计算需要裁剪的宽度
                crop_x = (scale_w - target_width) // 2
                crop_y = 0
                crop_filter = f",crop={target_width}:{target_height}:{crop_x}:{crop_y}"
                
                scale_ratio = target_height / v_height
            else:
                # 源视频更窄或相等，按宽度缩放并裁剪高度
                scale_w = target_width
                scale_h = int(v_height * (target_width / v_width))
                scale_filter = f"scale={scale_w}:{scale_h}:force_original_aspect_ratio=1"
                
                # 计算需要裁剪的高度
                crop_x = 0
                crop_y = (scale_h - target_height) // 2
                crop_filter = f",crop={target_width}:{target_height}:{crop_x}:{crop_y}"
                
                scale_ratio = target_width / v_width
            
            # 返回完整的缩放和裁剪滤镜
            return scale_filter + crop_filter, max(scale_ratio, 1.0)
        except Exception as e:
            logger.error(f"计算缩放模式时出错: {str(e)}")
            # 返回一个安全的默认值 - 使用填充模式避免裁剪错误
            return f"scale='min({target_width},iw)':'min({target_height},ih)':force_original_aspect_ratio=1,pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2:color=black", 1.0
    
    @staticmethod
    def get_video_info(video_path: str) -> Optional[dict]:
        """
        获取视频的完整信息
        
        Args:
            video_path: 视频文件路径
            
        Returns:
            包含视频信息的字典，如果获取失败则返回None
        """
        try:
            # 确保路径是绝对路径
            abs_path = os.path.abspath(video_path)
            logger.info(f"获取视频信息 - 路径: {abs_path}")
            
            if not os.path.exists(abs_path):
                logger.error(f"视频文件不存在: {abs_path}")
                return None
                
            # 检查文件是否可读
            try:
                with open(abs_path, 'rb') as f:
                    # 只读取前几个字节以验证文件可读
                    f.read(10)
            except Exception as e:
                logger.error(f"无法读取视频文件: {abs_path}, 错误: {str(e)}")
                return None
                
            # 检查文件大小
            file_size = os.path.getsize(abs_path)
            if file_size == 0:
                logger.error(f"视频文件大小为零: {abs_path}")
                return None
            
            logger.info(f"视频文件大小: {file_size} 字节")
                
            # 使用VideoMetadataExtractor获取视频元数据
            metadata = VideoMetadataExtractor.get_video_metadata(abs_path)
            
            # 验证元数据的有效性
            if not metadata:
                logger.error(f"获取视频元数据失败: {abs_path}")
                return None
                
            # 检查关键字段
            if metadata["width"] == 0 or metadata["height"] == 0:
                logger.error(f"无效的视频尺寸: 宽={metadata['width']}, 高={metadata['height']}")
                return None
                
            # 记录获取到的元数据
            logger.info(f"视频元数据: 宽={metadata['width']}, 高={metadata['height']}, " +
                      f"旋转={metadata['rotation']}°, 编码={metadata['codec']}")
                
            return metadata
        except Exception as e:
            logger.error(f"获取视频信息时出错: {str(e)}")
            return None

    @staticmethod
    def preprocess_video(materials: List[MaterialInfo], clip_duration=4, video_aspect: VideoAspect = VideoAspect.portrait):
        """
        处理视频和图片素材
        
        Args:
            materials: 素材信息列表
            clip_duration: 图片转视频的持续时间（秒）
            
        Returns:
            处理后的素材列表
        """
        # 安全检查：确保材料是有效的列表
        if materials is None:
            logger.warning("材料列表为空")
            return []
            
        # 处理非列表类型的错误参数情况
        if not isinstance(materials, list):
            logger.warning(f"材料参数类型错误: 预期列表，实际 {type(materials)}")
            # 如果是单个对象，尝试转换为列表
            if isinstance(materials, MaterialInfo):
                materials = [materials]
            else:
                logger.error(f"无法处理的材料类型: {type(materials)}")
                return []
                
        # 创建处理器实例
        processor = VideoPreprocessor()
        
        # 处理视频素材
        return processor._process_materials(materials, clip_duration, video_aspect)
    
    @staticmethod
    def preprocess_video_ffmpeg(materials: List[MaterialInfo], clip_duration=4):
        """
        使用ffmpeg处理视频和图片素材
        
        Args:
            materials: 素材信息列表
            clip_duration: 图片转视频的持续时间（秒）
            
        Returns:
            处理后的素材列表
        """
        # 直接调用preprocess_video，保持API一致性
        return VideoPreprocessor.preprocess_video(materials, clip_duration)
    
    def _process_materials(self, materials: List[MaterialInfo], clip_duration=4, video_aspect: VideoAspect = VideoAspect.portrait):
        """
        处理素材列表（实例方法）
        
        Args:
            materials: 素材信息列表
            clip_duration: 图片转视频的持续时间（秒）
            
        Returns:
            处理后的素材列表
        """
        # 计数器
        processed_count = 0
        failed_count = 0
        skipped_count = 0
        
        # 获取编码器
        encoder = self._get_optimal_encoder()
        logger.info(f"视频处理使用编码器: {encoder}")
        
        # 初始化视频元数据提取器
        self.video_metadata_extractor = VideoMetadataExtractor()
        
        for idx, material in enumerate(materials):
            try:
                # 跳过无效材料
                if not self._is_valid_material(material):
                    logger.warning(f"跳过无效素材: {getattr(material, 'url', '<无URL>')}")
                    skipped_count += 1
                    continue
                
                logger.info(f"处理素材 [{idx+1}/{len(materials)}]: {os.path.basename(material.url)}")
                
                # 根据文件类型处理
                ext = utils.parse_extension(material.url)
                
                if ext in const.FILE_TYPE_VIDEOS:
                    # 处理视频
                    success = self._process_video_file(material, encoder, clip_duration, video_aspect)
                    if success:
                        processed_count += 1
                    else:
                        failed_count += 1
                elif ext in const.FILE_TYPE_IMAGES:
                    # 处理图片转视频
                    success = self._process_image_file(material, clip_duration)
                    if success:
                        processed_count += 1
                    else:
                        failed_count += 1
                else:
                    logger.warning(f"不支持的文件类型: {ext}")
                    skipped_count += 1
            except Exception as e:
                logger.error(f"处理素材出错: {str(e)}")
                import traceback
                logger.debug(f"错误详情: {traceback.format_exc()}")
                failed_count += 1
        
        # 处理结果统计
        logger.info(f"视频处理完成: 总计{len(materials)}个, 成功{processed_count}个, "
                   f"失败{failed_count}个, 跳过{skipped_count}个")
        
        return materials
    
    def _process_video_file(self, material, encoder, clip_duration, video_aspect: VideoAspect = VideoAspect.portrait):
        """处理单个视频文件"""
        try:
            # 检查视频是否已经被处理过
            url = material.url
            file_name = os.path.basename(url)
            
            # 检查文件名是否已经包含处理标记
            if "_processed" in file_name or "proc_" in file_name:
                logger.info(f"文件已处理过，跳过处理: {file_name}")
                return True
            
            # 1. 提取视频信息
            video_info = self._extract_video_info(url)
            if not video_info:
                logger.error(f"无法获取视频信息: {os.path.basename(url)}")
                return False
            
            # 2. 判断是否需要处理
            process_config = self._determine_processing_needs(video_info, url, video_aspect)
            
            # 如果不需要处理，直接返回成功
            if not process_config.get("needs_processing", False):
                logger.info(f"视频无需处理: {os.path.basename(url)}")
                return True
                
            # 3. 执行视频处理
            result = self._execute_video_processing(material, video_info, process_config, encoder, clip_duration)
            
            # 4. 如果处理成功，确保更新material的url为新路径
            if result and hasattr(material, 'processed_path') and material.processed_path:
                material.url = material.processed_path
                
                # 将处理信息添加到元数据缓存
                if hasattr(video_info, 'to_dict'):
                    metadata = video_info.to_dict()
                    metadata["rotation_handled"] = True
                    metadata["is_preprocessed"] = True
                    metadata["preprocessed_path"] = material.processed_path
                    
                    # 更新缓存
                    from app.services.cache_manager import cache_manager
                    cache_manager.set_metadata(material.processed_path, "detailed", metadata)
                    
                    logger.info(f"已将处理信息添加到元数据缓存: {os.path.basename(material.processed_path)}")
            
            return result
        except Exception as e:
            logger.error(f"处理视频文件时出错: {str(e)}")
            return False
    
    def _process_image_file(self, material, clip_duration):
        """处理图片转视频"""
        try:
            video_file = f"{material.url}.mp4"
            
            # 确保输出目录存在
            output_dir = os.path.dirname(video_file)
            os.makedirs(output_dir, exist_ok=True)
            
            logger.info(f"将图片转换为视频: {os.path.basename(material.url)}")
            
            # 缩放效果：使用zoompan滤镜并确保填充1080x1920的目标区域
            image_cmd = [
                "ffmpeg", "-y",
                "-loop", "1",  # 循环输入
                "-i", material.url,
                "-vf", f"zoompan=z='min(zoom+0.0015,1.2)':d={int(clip_duration*30)}:fps=30:x='(iw-iw/zoom)/2':y='(ih-ih/zoom)/2',scale=1080:1920:force_original_aspect_ratio=1,crop=1080:1920,format=yuv420p",
                "-c:v", "libx264",
                "-t", str(clip_duration),
                "-pix_fmt", "yuv420p",
                video_file
            ]
            
            # 执行命令
            result = subprocess.run(
                image_cmd, 
                stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE,
                universal_newlines=True,
                encoding='utf-8',
                errors='replace',
                check=False
            )
            
            if result.returncode != 0:
                stderr_lines = result.stderr.splitlines()
                error_details = "\n".join(stderr_lines[-5:]) if stderr_lines else "未知错误"
                logger.error(f"图片转视频失败: {error_details}")
                return False
            
            # 检查生成的视频文件
            if os.path.exists(video_file) and os.path.getsize(video_file) > 0:
                material.url = video_file
                logger.info(f"图片转视频成功: {os.path.basename(video_file)}")
                return True
            else:
                logger.error(f"生成的视频文件无效: {video_file}")
                return False
        
        except Exception as e:
            logger.error(f"图片转视频时出错: {str(e)}")
            return False
    
    def _extract_video_info(self, video_path):
        """提取视频元数据信息"""
        try:
            # 使用VideoMetadataExtractor获取视频信息
            return VideoMetadataExtractor.get_video_metadata(video_path)
        except Exception as e:
            logger.error(f"提取视频信息失败: {str(e)}")
            return None
    
    def _get_optimal_encoder(self):
        """获取最优编码器（考虑GPU加速）"""
        from app.services.video_encoder import HardwareAccelerator
        return HardwareAccelerator.get_optimal_encoder()

    def _is_valid_material(self, material):
        """检查材料是否有效"""
        if not material.url or not os.path.exists(material.url):
            logger.warning(f"无效的视频路径: {getattr(material, 'url', '未设置')}")
            return False
        return True
    
    def _determine_processing_needs(self, video_info, file_path, video_aspect: VideoAspect = VideoAspect.portrait):
        """确定视频处理需求 - 统一旋转处理流程"""
        # 提取视频基本信息
        width = video_info.width
        height = video_info.height
        rotation = video_info.rotation
        codec = video_info.codec
        
        # 标准化旋转角度为90的倍数
        if rotation != 0:
            rotation = VideoMetadataExtractor.normalize_rotation(rotation)
            video_info.rotation = rotation
            logger.info(f"标准化旋转角度: {rotation}°")
        
        # 计算旋转后的有效尺寸
        effective_width, effective_height = width, height
        if rotation in [90, 270]:
            effective_width, effective_height = height, width
            logger.info(f"检测到旋转视频：实际尺寸{width}x{height}，有效尺寸{effective_width}x{effective_height}")
        
        # 确定目标尺寸
        target_width, target_height = video_aspect.to_resolution()
        
        # 初始化配置
        config = {
            "needs_processing": False,  # 是否需要任何处理
            "needs_rotation": False,    # 是否需要物理旋转
            "needs_scaling": False,     # 是否需要缩放
            "needs_padding": False,     # 是否需要填充
            "needs_encoding": False,    # 是否需要编码转换
            "needs_antialias": False,   # 是否需要抗锯齿处理
            "target_width": target_width,
            "target_height": target_height,
            "is_high_quality": False,   # 是否为高质量视频
            "is_4k": width * height >= 3840 * 2160,  # 是否为4K视频
            "bitrate_boost": 1.0        # 码率提升倍数
        }
        
        # 判断是否为高质量视频
        config["is_high_quality"] = (width >= 1920 or height >= 1080) or (effective_width >= 1920 or effective_height >= 1920)
        
        # 判断视频方向 (基于有效尺寸)
        is_portrait = effective_height > effective_width
        
        # 1. 判断是否需要旋转处理 - 统一使用物理旋转
        if rotation != 0:
            # 对所有旋转视频统一使用物理旋转
            config["needs_rotation"] = True
            config["needs_processing"] = True
            config["needs_antialias"] = True  # 添加抗锯齿处理
            logger.info(f"检测到视频旋转{rotation}°，将使用物理旋转")
            
            # 对旋转视频提升码率补偿细节损失
            if is_portrait:
                config["bitrate_boost"] = 1.2  # 竖屏旋转内容提升20%码率
                logger.info("竖屏旋转内容提升20%码率以补偿细节损失")
        
        # 2. 判断是否需要编码转换
        if codec.lower() != "h264":
            config["needs_encoding"] = True
            config["needs_processing"] = True
            logger.info(f"需要编码转换: {codec} -> h264")
        
        # 3. 判断是否需要缩放和填充
        # 检查尺寸是否匹配目标尺寸
        if effective_width != target_width or effective_height != target_height:
            config["needs_scaling"] = True
            config["needs_processing"] = True
            
            # 计算源视频和目标视频的宽高比
            source_ratio = effective_width / effective_height if effective_height > 0 else 1.0
            target_ratio = target_width / target_height if target_height > 0 else 1.0
            
            # 如果宽高比差异超过阈值，需要填充
            ratio_diff = abs(target_ratio - source_ratio)
            if ratio_diff > 0.01:
                config["needs_padding"] = True
                logger.info(f"需要填充处理：原始比例 {source_ratio:.2f}，目标比例 {target_ratio:.2f}")
        
        # 4. 特殊处理4K视频
        if config["is_4k"]:
            config["bitrate_boost"] *= 1.5  # 4K内容额外提升50%码率
            logger.info("4K视频内容提升50%码率以保持画质")
        
        logger.info(f"视频处理配置: {config}")
        return config

    def _execute_video_processing(self, material, video_info, config, encoder, clip_duration):
        """执行视频处理"""
        source_path = material.url
        output_dir = os.path.dirname(source_path)
        file_name = os.path.basename(source_path)
        base_name, ext = os.path.splitext(file_name)
        processed_path = os.path.join(output_dir, f"{base_name}_processed{ext}")
        
        # 根据配置构建滤镜字符串
        vf_filter = self._build_filter_string(video_info, config)
        
        # 判断是否使用GPU编码器
        is_gpu_encoder = "nvenc" in encoder or "qsv" in encoder or "amf" in encoder
        
        # 如果是GPU编码，强制执行GPU诊断
        if is_gpu_encoder and "nvenc" in encoder:
            logger.info("使用NVENC编码，进行GPU兼容性检查...")
            diagnostic = HardwareAccelerator.diagnose_gpu_issues()
            
            # 如果诊断显示NVENC不可用，立即切换到CPU编码
            if not diagnostic["nvenc_functional"]:
                logger.warning("NVENC功能测试失败，切换到CPU编码")
                encoder = "libx264"
                is_gpu_encoder = False
        
        # 获取硬件加速参数，对于GPU编码器使用特别简化的参数
        input_params = []
        if is_gpu_encoder:
            if "nvenc" in encoder:
                # 超简化NVIDIA参数
                input_params = ["-hwaccel", "cuda"]
                logger.info("使用简化NVIDIA硬件加速设置")
            else:
                input_params = HardwareAccelerator.optimize_input_parameters(encoder, source_path)
        
        # 使用更安全的编码参数
        encoder_params = {}
        if is_gpu_encoder:
            # GPU编码使用极简参数
            encoder_params = self._sanitize_gpu_params({}, encoder)
            
            # 添加基本码率控制
            width = config["target_width"]
            height = config["target_height"]
            bitrate = EncoderConfig.get_optimal_bitrate(width, height, config.get("is_4k", False))
            
            encoder_params["bitrate"] = bitrate
            encoder_params["maxrate"] = int(bitrate * 1.5)
            encoder_params["bufsize"] = int(bitrate * 2)
        else:
            # CPU编码使用标准参数
            encoder_params = {
                "preset": "medium",
                "crf": "23"
            }
        
        # 构建FFMPEG命令
        cmd = self._build_ffmpeg_command(
            source_path, 
            processed_path,
            encoder,
            vf_filter,
            input_params,
            encoder_params,
            config
        )
        
        # 执行命令
        success = self._run_ffmpeg_command(cmd)
        
        # 如果GPU编码失败，尝试CPU编码
        if not success and is_gpu_encoder:
            logger.warning(f"GPU编码失败，切换到CPU编码...")
            
            # 使用CPU编码参数
            cpu_params = {
                "preset": "medium", 
                "crf": "23"
            }
            
            # 构建CPU编码命令
            cpu_cmd = self._build_ffmpeg_command(
                source_path, 
                processed_path,
                "libx264",
                vf_filter,
                [],  # 无特殊输入参数
                cpu_params,
                config
            )
            
            success = self._run_ffmpeg_command(cpu_cmd)
        
        # 如果仍然失败，尝试直接复制流
        if not success:
            logger.warning("编码失败，尝试直接流复制...")
            copy_cmd = [
                "ffmpeg", "-y",
                "-i", source_path,
                "-c:v", "copy",
                "-c:a", "copy",
                processed_path
            ]
            success = self._run_ffmpeg_command(copy_cmd)
        
        # 处理成功后，更新材料路径
        if success and os.path.exists(processed_path) and os.path.getsize(processed_path) > 0:
            material.url = processed_path
            logger.success(f"视频处理成功: {os.path.basename(processed_path)}")
            return True
        else:
            logger.error(f"视频处理失败: {os.path.basename(source_path)}")
            return False

    def _build_filter_string(self, video_info, config):
        """构建视频滤镜字符串 - 优化旋转和缩放效果"""
        filters = []
        
        # 提取基本信息
        width = video_info.width
        height = video_info.height
        rotation = video_info.rotation
        
        # 计算有效尺寸 - 考虑旋转因素
        effective_width, effective_height = width, height
        if rotation in [90, 270]:
            effective_width, effective_height = height, width
        
        # 1. 旋转滤镜(如果需要物理旋转)
        # if config.get("needs_rotation", False):
        #     rotation_filters = {
        #         90: "transpose=2",     #  90度旋转 (需顺时针270度校正) -> transpose=2
        #         180: "hflip,vflip",  # 180度旋转 -> 垂直和水平翻转
        #         270: "transpose=0"     # 270度旋转 (需顺时针90度校正) -> transpose=0
        #     }
            
        #     if rotation in rotation_filters:
        #         filters.append(rotation_filters[rotation])
        #         logger.info(f"应用物理旋转滤镜: {rotation_filters[rotation]}")
        
        # 2. 抗锯齿处理(如果需要)
        if config.get("needs_antialias", False):
            # 添加smartblur滤镜以防止旋转引起的锯齿
            # 3:0.8:0 参数分别是：半径，强度，阈值
            filters.append("smartblur=3:0.8:0")
            logger.info("应用smartblur滤镜防止旋转引起的锯齿")
        
        # 3. 缩放和填充处理
        target_width = config.get("target_width", 1080)
        target_height = config.get("target_height", 1920)
        
        # 所有视频使用统一的缩放和填充逻辑
        if config.get("needs_scaling", False):
            # 计算最佳缩放比例，保持宽高比
            scale_ratio = min(target_width / effective_width, target_height / effective_height)
            scaled_width = int(effective_width * scale_ratio)
            scaled_height = int(effective_height * scale_ratio)
            
            # 高质量缩放处理
            # 使用lanczos算法以获得更好的缩放质量
            scale_filter = f"scale={scaled_width}:{scaled_height}:flags=lanczos"
            
            if config.get("needs_padding", False) or scaled_width != target_width or scaled_height != target_height:
                # 添加填充以适应目标分辨率
                pad_filter = f",pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2:color=black"
                scale_filter += pad_filter
                logger.info(f"应用缩放与填充: 缩放至{scaled_width}x{scaled_height}并填充至{target_width}x{target_height}")
            else:
                logger.info(f"应用精确缩放: {width}x{height} -> {target_width}x{target_height}")
            
            filters.append(scale_filter)
        
        # 4. 确保输出为yuv420p格式(兼容性更好)
        filters.append("format=yuv420p")
        
        # 组合所有滤镜
        filter_string = ",".join(filters) if filters else "null"
        logger.info(f"最终滤镜串: {filter_string}")
        return filter_string

    def _build_ffmpeg_command(self, input_path, output_path, encoder, vf_filter, input_params, encoder_params, config):
        """构建FFMPEG命令 - 智能码率控制"""
        cmd = ["ffmpeg", "-y"]
        
        # 添加输入参数
        cmd.extend(input_params)
        
        # 添加输入文件
        cmd.extend(["-i", input_path])
        
        # 添加视频滤镜
        if vf_filter and vf_filter != "null":
            cmd.extend(["-vf", vf_filter])
        
        # 添加视频编码器
        cmd.extend(["-c:v", encoder])
        
        # 应用智能码率提升 (如果配置中有指定)
        bitrate_boost = config.get("bitrate_boost", 1.0)
        if bitrate_boost > 1.0:
            # 提取并应用增强的码率参数
            if "bitrate" in encoder_params:
                original_bitrate = encoder_params["bitrate"]
                boosted_bitrate = int(original_bitrate * bitrate_boost)
                encoder_params["bitrate"] = boosted_bitrate
                logger.info(f"应用码率提升: {original_bitrate}k -> {boosted_bitrate}k (x{bitrate_boost:.2f})")
            
            if "maxrate" in encoder_params:
                encoder_params["maxrate"] = int(encoder_params["maxrate"] * bitrate_boost)
            
            if "bufsize" in encoder_params:
                encoder_params["bufsize"] = int(encoder_params["bufsize"] * bitrate_boost)
        
        # 提取并添加编码器特定参数
        bitrate = encoder_params.pop("bitrate", None)
        maxrate = encoder_params.pop("maxrate", None)
        bufsize = encoder_params.pop("bufsize", None)
        
        if bitrate:
            cmd.extend(["-b:v", f"{bitrate}k"])
        if maxrate:
            cmd.extend(["-maxrate", f"{maxrate}k"])
        if bufsize:
            cmd.extend(["-bufsize", f"{bufsize}k"])
        
        # 添加其他编码器参数
        for key, value in encoder_params.items():
            cmd.extend([f"-{key}", str(value)])
        
        # 音频处理
        cmd.extend(["-c:a", "aac", "-b:a", "128k"])
        
        # 清除旋转元数据，确保视频在所有播放器中正确显示
        cmd.extend(["-metadata:s:v", "rotate=0"])
        cmd.extend(["-metadata:s:v:0", "rotate=0"])
        
        # 添加输出文件
        cmd.append(output_path)
        
        logger.info(f"FFMPEG命令: {' '.join(cmd)}")
        return cmd

    def _run_ffmpeg_command(self, cmd):
        """执行FFMPEG命令并处理输出"""
        try:
            # 提取encoder信息用于日志
            encoder = "unknown"
            for i, arg in enumerate(cmd):
                if arg == "-c:v" and i+1 < len(cmd):
                    encoder = cmd[i+1]
                    break
            
            logger.info(f"使用编码器 {encoder} 执行命令")
            logger.debug(f"完整命令: {' '.join(cmd)}")
            
            process = subprocess.Popen(
                cmd, 
                stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE, 
                universal_newlines=True,
                encoding='utf-8',
                errors='replace'
            )
            
            # 收集完整错误输出
            stderr_output = []
            progress_shown = False
            
            # 实时显示处理进度
            for line in process.stderr:
                stderr_output.append(line)
                
                # 显示进度
                if "time=" in line and "bitrate=" in line:
                    if not progress_shown:
                        logger.info(f"处理进度: {line.strip()}")
                        progress_shown = True
                    else:
                        logger.debug(f"进度: {line.strip()}")
                
                # 显示错误
                if "Error" in line or "Invalid" in line or "failed" in line:
                    logger.warning(f"处理中警告: {line.strip()}")
            
            process.wait()
            
            if process.returncode != 0:
                logger.error(f"命令执行失败 (返回码: {process.returncode}), 错误详情:")
                error_shown = False
                
                for line in stderr_output:
                    if "Error" in line or "Invalid" in line or "failed" in line or "No such filter" in line:
                        logger.error(line.strip())
                        error_shown = True
                
                # 如果没有找到特定错误，显示最后几行
                if not error_shown and stderr_output:
                    logger.error("最后几行输出:")
                    for line in stderr_output[-5:]:
                        logger.error(line.strip())
                    
                return False
            
            logger.info(f"命令执行成功，使用编码器: {encoder}")
            return True
            
        except Exception as e:
            logger.error(f"执行命令时出错: {str(e)}")
            import traceback
            logger.debug(f"错误堆栈: {traceback.format_exc()}")
            return False

    def _build_simple_filter(self, video_info, config):
        """构建简化的视频滤镜字符串用于降级处理"""
        # 提取基本信息
        width = int(video_info.get("width", 0))
        height = int(video_info.get("height", 0))
        rotation = int(video_info.get("rotation", 0))
        
        # 确保目标尺寸有效
        target_width = int(config.get("target_width", 1920))
        target_height = int(config.get("target_height", 1080))
        
        # 根据旋转计算有效尺寸
        effective_width, effective_height = width, height
        if rotation in [90, 270, -90]:
            effective_width, effective_height = height, width
        
        # 避免无效的尺寸
        if effective_width <= 0: effective_width = 1
        if effective_height <= 0: effective_height = 1
        
        # 使用安全的填充模式
        return (f"scale=w='min({target_width},iw)':h='min({target_height},ih)':"
                f"force_original_aspect_ratio=1,"
                f"pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2:color=black,"
                f"format=yuv420p")

    def _sanitize_gpu_params(self, params, encoder):
        """优化GPU编码器参数，移除可能导致错误的参数"""
        safe_params = {}
        
        # 只保留最基本、最稳定的参数
        if "nvenc" in encoder:
            # NVENC - 仅保留核心参数
            safe_params = {
                "preset": "p2",  # 使用较低复杂度的预设
                "tune": "hq"     # 高质量调优
            }
        elif "qsv" in encoder:
            # QSV - 简化参数
            safe_params = {
                "preset": "medium"
            }
        elif "amf" in encoder:
            # AMF - 简化参数
            safe_params = {
                "quality": "speed"
            }
        else:
            # 复制原参数
            safe_params = params.copy()
        
        # 保留码率参数
        for key in ["bitrate", "maxrate", "bufsize"]:
            if key in params:
                safe_params[key] = params[key]
        
        logger.info(f"优化{encoder}参数: {safe_params}")
        return safe_params

