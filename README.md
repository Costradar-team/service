# service

## 로컬 Docker · 스키마

각자 PC에서 MySQL을 띄운 뒤 테이블을 만든다. 비밀번호는 `.env`. GitHub에 올리지 않는다.

```powershell
copy .env.example .env
docker compose up -d
.\scripts\apply_schema.ps1
```

- 호스트 포트 `3307` (`.env`의 `MYSQL_PORT`)
- 테이블 11개: KCA 7 (`retailer` 포함) + KAMIS 2 + FIS 2
- 처음 `docker compose up`만 하면 빈 볼륨에 SQL이 자동으로 들어간다. 이미 볼륨이 있으면 `apply_schema.ps1`을 쓴다.
- 적재된 행이 있으면 `apply_schema.ps1` / `001`을 다시 돌리지 않는다 (`DROP` 있음).

적재(processed CSV는 PR #13 이후 GitHub main에 있음):

```powershell
python data-pipeline\scripts\load\load_kca_mysql.py
python data-pipeline\scripts\load\load_kamis.py
python data-pipeline\scripts\load\load_fis_mysql.py
```

CSV 기본 경로: `data-pipeline/data/processed/kca|kamis|fis/`.

## 협업 규칙

- 브랜치 전략은 GitHub Flow를 따른다.
- 브랜치명은 `feature/`, `fix/`, `refactor/`, `docs/`, `test/`, `chore/` prefix를 사용한다.
- 커밋 메시지는 `feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:` 형식을 사용한다.
