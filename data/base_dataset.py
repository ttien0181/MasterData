import os
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Tuple
from data.models import RawRecord

class BaseDataset(ABC):
    """
    Abstract Base Class cho tất cả các dataset loader.
    Tất cả các loader dataset phải kế thừa class này để đảm bảo interface thống nhất.
    """
    
    @abstractmethod
    def load(self) -> Tuple[List[RawRecord], Dict[str, str]]:
        """
        Load dataset từ nguồn (local cache hoặc download từ internet).
        Returns:
            - List[RawRecord]: Danh sách các bản ghi thô đã convert sang format chung
            - Dict[str, str]: Ground truth: key là source_id, value là entity_id gốc (để benchmark)
        """
        pass
    
    @abstractmethod
    def get_dataset_info(self) -> Dict[str, Any]:
        """
        Trả về metadata về dataset:
        - name: Tên dataset
        - size: Tổng số bản ghi
        - source: Nguồn gốc dataset (FEBRL, NCVR, custom...)
        - description: Mô tả ngắn gọn về dataset
        - ground_truth_available: Có ground truth để benchmark không?
        """
        pass
    
    def save_to_cache(self, data: Any, cache_path: str) -> None:
        """Lưu dataset vào cache local để không phải download lại"""
        import pickle
        import os
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, 'wb') as f:
            pickle.dump(data, f)
    
    def load_from_cache(self, cache_path: str) -> Any:
        """Load dataset từ cache local nếu có"""
        import pickle

        from data.models import RawRecord

        class CacheUnpickler(pickle.Unpickler):
            def find_class(self, module: str, name: str) -> Any:
                if (module, name) == ("pipeline.models", "RawRecord"):
                    return RawRecord
                return super().find_class(module, name)

        if os.path.exists(cache_path):
            with open(cache_path, 'rb') as f:
                return CacheUnpickler(f).load()
        return None
