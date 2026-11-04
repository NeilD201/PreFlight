"""Store Gemini credentials in macOS Keychain, outside reports and browser storage.

Call Security.framework directly so secrets never enter a shell command or process
argument. Keychain controls encryption and access. No plaintext fallback is used.
"""
import ctypes as C
import getpass
import hashlib
from pathlib import Path
import sys

SERVICE=b'PCB Preflight Gemini'
META_SERVICE=b'preflightinapp'
NOT_FOUND=-25300


def meta_account():
    """macOS username owns the Meta API key entry."""
    return getpass.getuser()


class KeychainError(ValueError):
    pass


def available():
    return sys.platform=='darwin'


def account_for(reports):
    return hashlib.sha256(str(Path(reports).resolve()).encode()).hexdigest()[:32]


class Keychain:
    def __init__(self,account,service=SERVICE):
        if not available():raise KeychainError('Remembering keys currently requires macOS Keychain. You can still use a key for this session.')
        self.account=account.encode();self.service=service
        try:
            self.sec=C.CDLL('/System/Library/Frameworks/Security.framework/Security')
            self.cf=C.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        except OSError:
            raise KeychainError('macOS Keychain is unavailable. The key has not been saved.') from None
        ptr=C.c_void_p;u32=C.c_uint32
        self.sec.SecKeychainFindGenericPassword.argtypes=[ptr,u32,C.c_char_p,u32,C.c_char_p,C.POINTER(u32),C.POINTER(ptr),C.POINTER(ptr)]
        self.sec.SecKeychainAddGenericPassword.argtypes=[ptr,u32,C.c_char_p,u32,C.c_char_p,u32,ptr,C.POINTER(ptr)]
        self.sec.SecKeychainItemModifyAttributesAndData.argtypes=[ptr,ptr,u32,ptr]
        self.sec.SecKeychainItemFreeContent.argtypes=[ptr,ptr]
        self.sec.SecKeychainItemDelete.argtypes=[ptr]
        for name in ('SecKeychainFindGenericPassword','SecKeychainAddGenericPassword','SecKeychainItemModifyAttributesAndData','SecKeychainItemFreeContent','SecKeychainItemDelete'):
            getattr(self.sec,name).restype=C.c_int32
        self.cf.CFRelease.argtypes=[ptr];self.cf.CFRelease.restype=None

    @staticmethod
    def _check(status):
        if status:
            raise KeychainError(f'macOS Keychain could not complete the request (status {status}). Unlock your login keychain or allow access if macOS asks. The key was not saved to a file.')

    def _find(self,read=False):
        item=C.c_void_p();data=C.c_void_p();length=C.c_uint32()
        status=self.sec.SecKeychainFindGenericPassword(None,len(self.service),self.service,len(self.account),self.account,
            C.byref(length) if read else None,C.byref(data) if read else None,C.byref(item))
        if status==NOT_FOUND:return None,None
        self._check(status)
        try:
            if read and length.value>2048:raise KeychainError('The saved Keychain entry is invalid. Replace it in AI settings.')
            value=C.string_at(data,length.value).decode('utf-8') if read else None
            return item,value
        except (UnicodeError,ValueError):
            if item:self.cf.CFRelease(item)
            raise KeychainError('The saved Keychain entry could not be read. Replace it in AI settings.') from None
        finally:
            if data:self.sec.SecKeychainItemFreeContent(None,data)

    def load(self):
        item,value=self._find(read=True)
        if item:self.cf.CFRelease(item)
        return value or ''

    def save(self,key):
        if not isinstance(key,str) or not key or len(key)>2048 or any(ord(c)<33 or ord(c)>126 for c in key):
            raise KeychainError('Enter a complete API key before saving it.')
        encoded=key.encode();item,_=self._find()
        try:
            if item:
                status=self.sec.SecKeychainItemModifyAttributesAndData(item,None,len(encoded),C.c_char_p(encoded))
            else:
                status=self.sec.SecKeychainAddGenericPassword(None,len(self.service),self.service,len(self.account),self.account,len(encoded),C.c_char_p(encoded),None)
            self._check(status)
        finally:
            if item:self.cf.CFRelease(item)

    def delete(self):
        item,_=self._find()
        if item:
            try:self._check(self.sec.SecKeychainItemDelete(item))
            finally:self.cf.CFRelease(item)
