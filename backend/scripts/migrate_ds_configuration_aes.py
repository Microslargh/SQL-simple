import sys

from sqlmodel import Session, select

from apps.datasource.models.datasource import CoreDatasource
from apps.datasource.utils.utils import aes_decrypt, aes_encrypt
from apps.db.engine import get_engine_conn


def main() -> None:
    """
    一次性迁移脚本：
    - 读取 core_datasource.configuration 旧密文
    - 使用兼容版 aes_decrypt 解密为明文
    - 使用新算法 aes_encrypt（AES-GCM）重新加密
    - 更新回数据库

    注意：
    - 请在执行前做好数据库备份！
    - 只需要在维护窗口执行一次即可。
    """
    engine = get_engine_conn()

    migrated = 0
    failed = 0

    with Session(engine) as session:
        ds_list = session.exec(select(CoreDatasource)).all()
        for ds in ds_list:
            # 跳过空配置
            if not ds.configuration:
                continue

            try:
                # 使用当前兼容版 aes_decrypt 解出明文
                plain = aes_decrypt(ds.configuration)

                # 使用新算法（AES-GCM）重新加密
                new_cipher = aes_encrypt(plain)

                # 存回字符串形式
                ds.configuration = new_cipher.decode("utf-8")
                session.add(ds)
                migrated += 1
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"[ERROR] migrate ds id={ds.id} failed: {e}", file=sys.stderr)

        session.commit()

    print(f"[DONE] migrated={migrated}, failed={failed}")


if __name__ == "__main__":
    main()


