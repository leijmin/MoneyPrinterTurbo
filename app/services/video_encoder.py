
class EncoderConfig:
    """编码器配置类"""
    @staticmethod
    def get_optimal_bitrate(width: int, height: int, is_4k: bool = False) -> int:
        """计算最优码率（kbps）"""
        pixel_count = width * height
        
        # 超高清视频 (4K及以上)
        if is_4k:
            base_bitrate = 30000  # 30Mbps基准
        # 高清视频 (1080p-2K)
        elif pixel_count >= 1920 * 1080:
            base_bitrate = 15000  # 15Mbps基准
        # 标清视频
        else:
            base_bitrate = 8000   # 8Mbps基准
            
        # 根据像素数精确调整码率
        adjusted_bitrate = int((pixel_count / (1920 * 1080)) * base_bitrate)
        
        # 设置合理的下限和上限
        min_bitrate = 6000   # 最低保证6Mbps
        max_bitrate = 40000  # 最高不超过40Mbps
        
        # 确保码率在合理范围内
        return max(min_bitrate, min(adjusted_bitrate, max_bitrate))
    
    @staticmethod
    def get_encoder_params(hw_accel: str, width: int, height: int) -> dict:
        """获取编码器参数"""
        is_4k = width * height >= 3840 * 2160
        bitrate = EncoderConfig.get_optimal_bitrate(width, height, is_4k)
        
        # 基础参数
        params = {
            "bitrate": bitrate,
            "maxrate": int(bitrate * 1.5),
            "bufsize": int(bitrate * 3),  # 增大缓冲区
            "refs": 6 if is_4k else 5,    # 增加参考帧数量
            "g": 30,  # GOP大小
        }
        
        # 根据不同硬件加速器优化参数
        if hw_accel == "h264_nvenc":
            params.update({
                "preset": "p7",    # 最高质量预设
                "tune": "hq",      # 高质量调优
                "rc": "vbr",       # 使用可变码率模式
                "cq": 15,          # 降低质量参数以提高画质
                "qmin": 10,        # 最小量化参数
                "qmax": 25,        # 最大量化参数
                "profile:v": "high",
                # 完全移除level参数，让NVENC自动选择合适的level
                "spatial-aq": "1", # 空间自适应量化
                "temporal-aq": "1", # 时间自适应量化
                "rc-lookahead": "32", # 前瞻帧数
                "surfaces": "32"    # 表面缓冲区
            })
        elif hw_accel == "h264_qsv":
            params.update({
                "preset": "veryslow", # 最慢压缩 = 最高质量
                "look_ahead": "1",
                "global_quality": 15, # 高质量参数
                "profile:v": "high"
                # 移除level参数
            })
        elif hw_accel == "h264_amf":
            params.update({
                "quality": "quality",
                "profile:v": "high",
                # 移除level参数
                "refs": 6 if is_4k else 5,  # 增加参考帧
                "preanalysis": "1",
                "vbaq": "1",  # 启用方差基础自适应量化
                "enforce_hrd": "1"
                # 移除可能引起问题的参数
            })
        else:  # libx264软件编码
            params.update({
                "preset": "slow",  # 慢速预设，提高质量
                "crf": "18",       # 高质量CRF值
                "profile:v": "high",
                # 移除level参数
                "refs": 5,         # 参考帧数量
                "psy": "1",        # 启用心理视觉优化
                "psy-rd": "1.0:0.05"  # 心理视觉优化率失真
            })
        
        # 为4K视频添加额外参数
        if is_4k:
            # 增加4K视频的颜色属性和质量设置
            extra_params = {
                "pix_fmt": "yuv420p10le" if hw_accel == "libx264" else "yuv420p", # 10-bit色深(仅软件编码)
                "colorspace": "bt709",     # 标准色彩空间
                "color_primaries": "bt709", # 色彩原色
                "color_trc": "bt709",       # 色彩传输特性
                "movflags": "+faststart"    # 文件元数据前置，便于快速播放
            }
            params.update(extra_params)
        
        return params

