# backend

로컬 Docker MySQL 스키마. 1차 화면 API는 KCA만 쓴다. KAMIS·FIS 테이블은 적재용이다.

JWT, 저장 리스트, 지역 필터는 만들지 않는다.

## 스키마

- `sql/001_kca_schema.sql` — KCA 7테이블. `retailer` 포함.
- `sql/002_kamis_fis_schema.sql` — KAMIS 2 + FIS 2. `canonical_item` 필요.

Docker MySQL이 켜진 뒤 (호스트 3307). 저장소 루트에서:

```powershell
docker compose up -d
.\scripts\apply_schema.ps1
```

`001` 다음에 `002`가 적용된다. 적재된 행이 있으면 다시 돌리지 않는다.
