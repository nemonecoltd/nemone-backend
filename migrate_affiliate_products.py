"""쿠팡 파트너스 상품 추천 — 멱등(재실행 안전).

AffiliateProduct 모델 자체는 main.py의 Base.metadata.create_all()이 앱 기동 시
자동 생성하지만(신규 테이블), 기존 posts 테이블에 컬럼을 추가하는 건
create_all이 못 하므로 이 스크립트로 처리한다. plants 서비스의 동일 스크립트와
같은 패턴.
"""
import os

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

# main.py와 동일한 방식으로 접속 정보 구성(단일 DATABASE_URL이 아니라 개별 env var)
SQLALCHEMY_DATABASE_URL = (
    f"postgresql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}"
    f"@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT', '5432')}/{os.getenv('DB_NAME')}"
)
engine = create_engine(SQLALCHEMY_DATABASE_URL)

STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS affiliate_products (
        id SERIAL PRIMARY KEY,
        label VARCHAR NOT NULL,
        coupang_url VARCHAR NOT NULL,
        image_url VARCHAR,
        match_keywords VARCHAR[] NOT NULL DEFAULT '{}',
        sort_order INTEGER NOT NULL DEFAULT 0,
        is_active BOOLEAN NOT NULL DEFAULT true,
        created_at TIMESTAMP DEFAULT now(),
        updated_at TIMESTAMP DEFAULT now()
    )
    """,
    "ALTER TABLE posts ADD COLUMN IF NOT EXISTS affiliate_product_id INTEGER",
]


def run() -> None:
    with engine.begin() as conn:
        for stmt in STATEMENTS:
            conn.execute(text(stmt))
        # FK는 컬럼 추가와 별개 스텝으로 — 이미 있으면 건너뜀(멱등)
        exists = conn.execute(
            text(
                """
                SELECT 1 FROM information_schema.table_constraints
                WHERE constraint_name = 'fk_posts_affiliate_product'
                """
            )
        ).first()
        if not exists:
            conn.execute(
                text(
                    """
                    ALTER TABLE posts
                    ADD CONSTRAINT fk_posts_affiliate_product
                    FOREIGN KEY (affiliate_product_id) REFERENCES affiliate_products(id)
                    ON DELETE SET NULL
                    """
                )
            )
    print("완료: affiliate_products 테이블 + posts.affiliate_product_id 컬럼/FK 준비됨")


if __name__ == "__main__":
    run()
