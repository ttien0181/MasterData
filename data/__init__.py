from typing import Dict, Type
from data.base_dataset import BaseDataset
from data.febrl_loader import FEBRLLoader

class DatasetFactory:
    """
    Factory class để tạo đúng dataset loader theo tên.
    Hỗ trợ thêm dataset mới dễ dàng bằng cách thêm vào _registry.
    """
    
    # Registry các loader hỗ trợ
    _registry: Dict[str, Type[BaseDataset]] = {
        "febrl3": FEBRLLoader,
        "febrl4": FEBRLLoader,
    }
    
    @classmethod
    def get_loader(cls, dataset_name: str) -> BaseDataset:
        """
        Lấy dataset loader theo tên.
        Args:
            dataset_name: Tên dataset, ví dụ: "febrl3", "febrl4", "ncvr"
        Returns:
            Instance của dataset loader
        Raises:
            ValueError: Nếu dataset không được hỗ trợ
        """
        if dataset_name not in cls._registry:
            supported = ", ".join(cls._registry.keys())
            raise ValueError(
                f"Dataset '{dataset_name}' chưa được hỗ trợ. "
                f"Các dataset hiện có: {supported}"
            )
        
        # Với FEBRLLoader, truyền version vào constructor
        if dataset_name in ["febrl3", "febrl4"]:
            return cls._registry[dataset_name](dataset_version=dataset_name)
        
        # Các dataset khác nếu có
        return cls._registry[dataset_name]()
    
    @classmethod
    def list_available_datasets(cls) -> list:
        """Trả về danh sách tất cả các dataset có sẵn"""
        return list(cls._registry.keys())