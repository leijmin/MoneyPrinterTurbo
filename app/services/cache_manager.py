import os
import hashlib
from typing import Dict, Any, Optional
from loguru import logger

class VideoCacheManager:
    """视频元数据缓存管理器，使用内存存储"""
    
    # 单例模式
    _instance = None
    
    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(VideoCacheManager, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self, ttl=86400):
        """
        初始化缓存管理器
        
        Args:
            ttl: 缓存过期时间（秒），默认为24小时，实际只用于日志显示
        """
        if self._initialized:
            return
        
        self._memory_cache = {}  # 内存缓存
        self.ttl = ttl          # 缓存过期时间（仅作记录）
        self._initialized = True
        logger.info("✅ 初始化内存缓存管理器")
    
    def generate_key(self, file_path: str, metadata_type: str) -> str:
        """
        生成缓存键
        
        Args:
            file_path: 文件路径
            metadata_type: 元数据类型（basic或detailed）
            
        Returns:
            缓存键
        """
        # 获取文件的绝对路径
        abs_path = os.path.abspath(file_path)
        
        # 仅使用文件名和文件属性，避免完整路径可能导致的不一致
        file_name = os.path.basename(abs_path)
        
        # 使用文件属性
        try:
            mtime = os.path.getmtime(abs_path)
            file_size = os.path.getsize(abs_path) 
        except (OSError, IOError):
            mtime = 0
            file_size = 0
        
        # 使用文件名+大小+修改时间+类型 作为键的基础
        key_base = f"{file_name}:{file_size}:{mtime}:{metadata_type}"
        
        # 简单哈希
        hash_obj = hashlib.md5()
        hash_obj.update(key_base.encode('utf-8'))
        hash_hex = hash_obj.hexdigest()
        
        key = f"video:metadata:{hash_hex}"
        logger.debug(f"生成缓存键: {key} (基于 {file_name})")
        
        return key
    
    def get_metadata(self, file_path: str, metadata_type: str) -> Optional[Dict[str, Any]]:
        """
        获取缓存的元数据
        
        Args:
            file_path: 文件路径
            metadata_type: 元数据类型（basic或detailed）
            
        Returns:
            缓存的元数据字典，如果不存在则返回None
        """
        if not self._initialized:
            return None
            
        key = self.generate_key(file_path, metadata_type)
        
        try:
            # 从内存缓存获取
            if key in self._memory_cache:
                logger.info(f"✅ 命中缓存! 读取元数据: {os.path.basename(file_path)}")
                return self._memory_cache[key]
                
            logger.info(f"❗ 缓存未命中: {os.path.basename(file_path)}")
        except Exception as e:
            logger.error(f"❌ 读取缓存失败: {str(e)}")
        
        return None
    
    def set_metadata(self, file_path: str, metadata_type: str, metadata: Dict[str, Any]) -> bool:
        """
        设置缓存元数据
        
        Args:
            file_path: 文件路径
            metadata_type: 元数据类型（basic或detailed）
            metadata: 要缓存的元数据字典
            
        Returns:
            是否成功设置缓存
        """
        if not self._initialized or not metadata:
            return False
            
        key = self.generate_key(file_path, metadata_type)
        
        try:
            # 存入内存缓存
            self._memory_cache[key] = metadata
            logger.info(f"✅ 元数据已写入内存缓存: {os.path.basename(file_path)}")
            return True
        except Exception as e:
            logger.error(f"❌ 写入缓存失败: {str(e)}")
            return False
    
    def invalidate(self, file_path: str = None) -> None:
        """
        使缓存失效
        
        Args:
            file_path: 如果提供，则只清除该文件的缓存；否则清除所有缓存
        """
        if not self._initialized:
            return
            
        try:
            if file_path:
                # 清除特定文件的缓存
                basic_key = self.generate_key(file_path, "basic")
                detailed_key = self.generate_key(file_path, "detailed")
                
                if basic_key in self._memory_cache:
                    del self._memory_cache[basic_key]
                if detailed_key in self._memory_cache:
                    del self._memory_cache[detailed_key]
                    
                logger.debug(f"🗑️ 清除文件缓存: {os.path.basename(file_path)}")
            else:
                # 清除所有缓存
                self._memory_cache = {}
                logger.debug("🗑️ 清除所有视频元数据缓存")
        except Exception as e:
            logger.error(f"❌ 清除缓存失败: {str(e)}")

    def debug_cache_info(self, file_path: str = None):
        """
        打印缓存调试信息
        
        Args:
            file_path: 如果提供，则只显示该文件的缓存信息
        """
        if not self._initialized:
            logger.info("缓存未初始化")
            return
        
        try:
            if file_path:
                # 显示特定文件的缓存信息
                basic_key = self.generate_key(file_path, "basic")
                detailed_key = self.generate_key(file_path, "detailed")
                
                basic_exists = basic_key in self._memory_cache
                detailed_exists = detailed_key in self._memory_cache
                
                logger.info(f"文件 {os.path.basename(file_path)} 的缓存状态:")
                logger.info(f"  - 基础元数据缓存: {'存在' if basic_exists else '不存在'}")
                logger.info(f"  - 详细元数据缓存: {'存在' if detailed_exists else '不存在'}")
            else:
                # 显示所有缓存信息
                memory_cache_keys = list(self._memory_cache.keys())
                logger.info(f"内存缓存中共有 {len(memory_cache_keys)} 条记录")
                for key in memory_cache_keys[:10]:
                    logger.info(f"  - {key}")
                
                if len(memory_cache_keys) > 10:
                    logger.info(f"  ... 还有 {len(memory_cache_keys) - 10} 条记录未显示")
        except Exception as e:
            logger.error(f"获取缓存信息失败: {str(e)}")


# 创建缓存管理器实例
cache_manager = VideoCacheManager() 