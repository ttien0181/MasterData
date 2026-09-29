import random
from pathlib import Path
from typing import List, Dict, Any, Tuple
from data.models import RawRecord
from data.base_dataset import BaseDataset

class FEBRLLoader(BaseDataset):
    """
    Loader cho các FEBRL dataset (febrl3, febrl4).
    FEBRL3: 5000 records, 2000 originals, 3000 duplicates
    FEBRL4: 2 datasets 4A (5000) và 4B (5000), dùng cho record linkage giữa 2 hệ thống
    """
    
    def __init__(self, dataset_version: str = "febrl3"):
        """
        Args:
            dataset_version: "febrl3" hoặc "febrl4"
        """
        self.dataset_version = dataset_version
        self.cache_path = str(Path(__file__).resolve().parent / "cache" / f"{dataset_version}.pkl")
        # Danh sách hệ thống nguồn để mô phỏng đa nguồn (ERP, CRM, Procurement...)
        self.source_systems = ["ERP", "CRM", "PROCUREMENT", "BILLING", "SUPPORT"]
    
    def load(self) -> Tuple[List[RawRecord], Dict[str, str]]:
        """Load FEBRL dataset, trả về list RawRecord và ground truth"""
        # Thử load từ cache trước
        cached_data = self.load_from_cache(self.cache_path)
        if cached_data:
            print(f"Loaded {self.dataset_version} from cache")
            return cached_data
        
        # Nếu không có cache, download từ recordlinkage
        try:
            import pandas as pd
            from recordlinkage.datasets import load_febrl3, load_febrl4
        except ImportError as exc:
            raise RuntimeError(
                "Cần cài pandas và recordlinkage để tạo lại cache FEBRL: "
                "uv pip install pandas recordlinkage"
            ) from exc
        print(f"Loading {self.dataset_version} dataset...")
        if self.dataset_version == "febrl3":
            # load_febrl3() trả về 1 DataFrame duy nhất, cần load ground truth riêng
            df = load_febrl3()
            # FEBRL3 ground truth được tích hợp trong dataframe, lấy index làm ground truth gốc
            ground_truth = []
            # Với FEBRL3, các record có cùng 'rec_id' gốc là trùng lặp
            # Tạo các cặp ground truth từ dataframe
            from collections import defaultdict
            original_ids = defaultdict(list)
            for idx, row in df.iterrows():
                # rec_id gốc (trước khi thêm _duplicate)
                orig_id = str(row.name).split('-')[0] + '-' + str(row.name).split('-')[1]
                original_ids[orig_id].append(str(row.name))
            # Tạo các cặp từ các nhóm
            for ids in original_ids.values():
                if len(ids) >= 2:
                    # Tạo tất cả các cặp trong nhóm
                    for i in range(len(ids)):
                        for j in range(i+1, len(ids)):
                            ground_truth.append((ids[i], ids[j]))
        elif self.dataset_version == "febrl4":
            # Với febrl4, ground truth được cung cấp sẵn dưới dạng các cặp index
            df_a, df_b, ground_truth_links = load_febrl4(return_links=True)
            # Nối 2 dataframe lại
            df = pd.concat([df_a, df_b])
            # Chuyển đổi ground_truth từ MultiIndex sang list of tuples
            ground_truth = [(str(i), str(j)) for i, j in ground_truth_links]
        else:
            raise ValueError(f"Chỉ hỗ trợ febrl3 và febrl4, không hỗ trợ {self.dataset_version}")
        
        # Convert sang list RawRecord
        raw_records: List[RawRecord] = []
        ground_truth_map: Dict[str, str] = {}
        
        # Duyệt qua từng record trong dataframe trước để tạo all_source_ids
        all_source_ids = []
        for idx, row in df.iterrows():
            all_source_ids.append(str(idx))
        
        # Tạo mapping source_id -> true entity_id từ ground_truth
        # Với FEBRL3, ground_truth chứa các cặp (original_rec_id, duplicate_rec_id)
        # Cần build connected components để gom tất cả các record cùng cluster vào 1 group
        from collections import defaultdict, deque
        adj: Dict[str, list] = defaultdict(list)
        for record_id1, record_id2 in ground_truth:
            # record_id1 và record_id2 là index gốc của dataframe, cần convert sang str
            rid1 = str(record_id1)
            rid2 = str(record_id2)
            adj[rid1].append(rid2)
            adj[rid2].append(rid1)
        
        # BFS để tìm connected components
        visited = set()
        cluster_id_counter = 1
        for rid in all_source_ids:
            if rid not in visited and rid in adj:
                # BFS
                q = deque([rid])
                visited.add(rid)
                current_cluster = f"true_cluster_{cluster_id_counter:04d}"
                while q:
                    curr = q.popleft()
                    ground_truth_map[curr] = current_cluster
                    for neighbor in adj[curr]:
                        if neighbor not in visited:
                            visited.add(neighbor)
                            q.append(neighbor)
                cluster_id_counter += 1
        
        # Các record không có trong ground_truth (không trùng với ai) tự tạo cluster riêng
        for rid in all_source_ids:
            if rid not in ground_truth_map:
                ground_truth_map[rid] = f"true_cluster_{cluster_id_counter:04d}"
                cluster_id_counter += 1
        
        # Duyệt qua từng record trong dataframe để tạo RawRecord
        for idx, row in df.iterrows():
            # Gán ngẫu nhiên một hệ thống nguồn để mô phỏng đa nguồn
            source_system = random.choice(self.source_systems)
            source_id = str(idx)
            
            # Tạo RawRecord
            # Xử lý tất cả trường hợp NaN, chuyển thành None an toàn cho Pydantic
            record = RawRecord(
                system=source_system,
                source_id=source_id,
                given_name=str(row['given_name']) if pd.notna(row.get('given_name')) else None,
                surname=str(row['surname']) if pd.notna(row.get('surname')) else None,
                address_1=str(row['address_1']) if pd.notna(row.get('address_1')) else None,
                address_2=str(row['address_2']) if pd.notna(row.get('address_2')) else None,
                suburb=str(row['suburb']) if pd.notna(row.get('suburb')) else None,
                postcode=str(row.get('postcode', '')) if pd.notna(row.get('postcode')) else None,
                state=str(row['state']) if pd.notna(row.get('state')) else None,
                date_of_birth=str(row.get('date_of_birth', '')) if pd.notna(row.get('date_of_birth')) else None,
                # Gán full address cho compatibility
                address=self._build_full_address(row),
                phone=str(row['phone_number']) if pd.notna(row.get('phone_number')) else None
            )
            raw_records.append(record)
        
        # Cache lại dữ liệu
        self.save_to_cache((raw_records, ground_truth_map), self.cache_path)
        print(f"Loaded {len(raw_records)} records from {self.dataset_version}")
        return raw_records, ground_truth_map
    
    def _build_full_address(self, row: Any) -> str:
        """Tạo địa chỉ đầy đủ từ các trường của FEBRL"""
        import pandas as pd
        address_parts = []
        if pd.notna(row.get('street_number')):
            address_parts.append(str(row['street_number']))
        if pd.notna(row.get('address_1')):
            address_parts.append(row['address_1'])
        if pd.notna(row.get('address_2')):
            address_parts.append(row['address_2'])
        if pd.notna(row.get('suburb')):
            address_parts.append(row['suburb'])
        if pd.notna(row.get('postcode')):
            address_parts.append(str(row['postcode']))
        if pd.notna(row.get('state')):
            address_parts.append(row['state'])
        return ", ".join(filter(None, address_parts))
    
    def get_dataset_info(self) -> Dict[str, Any]:
        if self.dataset_version == "febrl3":
            return {
                "name": "FEBRL3",
                "size": 5000,
                "source": "FEBRL",
                "description": "Dataset 5000 records cá nhân Úc, 2000 bản gốc, 3000 bản trùng",
                "ground_truth_available": True
            }
        else:  # febrl4
            return {
                "name": "FEBRL4",
                "size": 10000,
                "source": "FEBRL",
                "description": "Dataset 2 nguồn 5000 records mỗi nguồn, dùng cho record linkage",
                "ground_truth_available": True
            }
