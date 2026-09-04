# ETL Conventions

## Idempotency

- 동일 원천 데이터를 재실행해도 중복 적재되지 않아야 한다.
- DB `UNIQUE` constraint를 중복 방지의 최종 기준으로 사용한다.
- 기존 데이터는 데이터셋별 정책에 따라 UPSERT 또는 SKIP한다.

## Load Order

- FK 부모에서 자식 순서로 적재한다.
- 자식 FK에는 원천의 임시 식별자가 아닌 DB에서 조회한 실제 PK를 사용한다.

## Transaction

- 논리적으로 함께 성공해야 하는 작업은 동일 transaction으로 처리한다.
- 대량 observation은 batch 단위로 적재하고 commit한다.
- batch 일부가 실패하거나 실행이 중단된 후에도 동일 입력을 안전하게 재실행할 수 있어야 한다.

## Transform / Load

- 정제, 매핑, 데이터 품질 검증은 Transform에서 수행한다.
- Load는 구조 검증과 DB 적재에 집중한다.
- 데이터 품질 문제는 `rejected`, `unmapped`, `conflict`로 분리한다.
- FK, schema, constraint 등 구조적 오류는 해당 transaction 또는 batch를 실패 처리한다.

## Observation

- observation의 `UNIQUE` key는 실제 데이터 grain을 기준으로 정의한다.
- 수집 시각을 key로 사용하는 경우 load 시각이 아닌 원본 수집 시각을 보존한다.
