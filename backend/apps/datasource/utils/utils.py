from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes
from Crypto.Util.Padding import unpad
import base64
from typing import Union

from common.core.config import settings


#
# 一、配置读取：新算法使用的 KEY / IV
#

def load_aes_key() -> bytes:
    """
    从配置加载 AES 密钥，支持：
    - 直接 16/24/32 长度的明文字符串（自动转 utf-8 bytes，推荐）
    - 也兼容 Base64 编码的密钥
    """
    raw = settings.AS_S_K
    if not raw:
        raise ValueError("AES secret key not configured")

    # 先按明文字符串处理，满足你“配置 16 位字符串”的需求
    key = raw.encode("utf-8")

    # 如果长度不合法，再尝试当作 Base64 解码
    if len(key) not in (16, 24, 32):
        try:
            key = base64.b64decode(raw)
        except Exception:
            raise ValueError("AES secret key length must be 16/24/32 bytes")

    if len(key) not in (16, 24, 32):
        raise ValueError("AES secret key length must be 16/24/32 bytes")
    return key


def load_aes_iv() -> bytes:
    """
    从配置加载 GCM 使用的 IV（nonce 前缀），要求：
    - 配置为 Base64 字符串
    - 解码后长度为 12 字节（GCM 推荐 nonce 长度）
    """
    raw = settings.AES_IV
    if not raw:
        raise ValueError("AES IV not configured")
    try:
        iv = base64.b64decode(raw)
    except Exception:
        raise ValueError("AES IV must be base64 encoded")
    if len(iv) != 12:
        raise ValueError("AES IV length must be 12 bytes for GCM")
    return iv


# 新算法使用的全局 KEY / 基础 IV
_GCM_KEY = load_aes_key()
_GCM_BASE_IV = load_aes_iv()


#
# 二、旧算法（ECB）解密：仅用于兼容历史数据
#

# 历史数据使用的固定 key，不再用于加密，只用于解密老数据


#
# 三、新算法（GCM）：用于新的加解密
#

def _mix_nonce(base_iv: bytes) -> bytes:
    """
    生成最终 nonce：使用配置的 IV 与随机字节异或，确保每次唯一且不可预测。
    """
    rand = get_random_bytes(12)
    return bytes(a ^ b for a, b in zip(base_iv, rand))


def aes_encrypt(data: str) -> bytes:
    """
    使用 AES-GCM 加密并返回 Base64 编码的字节串。
    - 明文：utf-8 编码字符串
    - 输出：Base64( nonce | ciphertext | tag )
    """
    plaintext = data.encode("utf-8")
    nonce = _mix_nonce(_GCM_BASE_IV)  # 12 字节 nonce（混合配置 IV 与随机源）
    cipher = AES.new(_GCM_KEY, AES.MODE_GCM, nonce=nonce)
    ciphertext, tag = cipher.encrypt_and_digest(plaintext)
    encrypted = nonce + ciphertext + tag  # 拼接 nonce | ciphertext | tag
    return base64.b64encode(encrypted)


def aes_decrypt(encrypted_data: Union[bytes, str]) -> str:
    """
    解密逻辑（向后兼容）：
    1. 优先按新算法 AES-GCM 尝试解密（Base64(nonce|ciphertext|tag)）
    2. 若失败，则回退到旧算法 AES-ECB 解密（兼容前端 CryptoJS）
    """
    if isinstance(encrypted_data, str):
        encrypted_data = encrypted_data.encode("utf-8")

    # 先尝试新格式：Base64( nonce | ciphertext | tag )
    try:
        raw = base64.b64decode(encrypted_data)
        # 长度太短，不可能是 GCM 格式，直接认为是旧格式
        if len(raw) < 12 + 16:  # nonce(12) + tag(16)
            raise ValueError("maybe legacy")

        nonce = raw[:12]
        tag = raw[-16:]
        ciphertext = raw[12:-16]
        cipher = AES.new(_GCM_KEY, AES.MODE_GCM, nonce=nonce)
        plaintext = cipher.decrypt_and_verify(ciphertext, tag)
        return plaintext.decode("utf-8")
    except Exception as gcm_error:
        # 新算法失败 -> 回退到旧算法 AES-ECB 解密（兼容前端 CryptoJS）
        try:
            # 前端使用 CryptoJS.AES.encrypt，密钥是 'SQLBot1234567890' (16字节)
            # CryptoJS 的 ECB 模式不使用 Salt，输出格式：Base64 编码的密文
            legacy_key = b'SQLBot1234567890'  # 16字节密钥
            
            # 解码 Base64（CryptoJS 的 toString() 返回 Base64 字符串）
            if isinstance(encrypted_data, bytes):
                encrypted_str = encrypted_data.decode("utf-8")
            else:
                encrypted_str = encrypted_data if isinstance(encrypted_data, str) else encrypted_data.decode("utf-8")
            
            # CryptoJS 的 Base64 字符串，直接解码
            ciphertext = base64.b64decode(encrypted_str)
            
            # ECB 模式解密（CryptoJS ECB 不使用 Salt）
            cipher = AES.new(legacy_key, AES.MODE_ECB)
            plaintext = unpad(cipher.decrypt(ciphertext), AES.block_size)
            
            return plaintext.decode("utf-8")
        except Exception as ecb_error:
            # 如果所有解密方法都失败，抛出异常而不是返回错误字符串
            error_msg = (
                f"Failed to decrypt datasource configuration. "
                f"GCM error: {str(gcm_error)}. "
                f"ECB error: {str(ecb_error)}. "
                f"Data may be corrupted, empty, or encrypted with an unsupported method."
            )
            raise ValueError(error_msg) from ecb_error