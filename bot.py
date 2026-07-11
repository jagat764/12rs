#!/usr/bin/env python3

import requests
import json
import time
import random
import re
import os
import logging
from datetime import datetime
from typing import Optional, Dict, List
from urllib.parse import urljoin, parse_qs, urlparse
import hashlib
import threading

# ============ TELEGRAM IMPORTS ============
try:
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
    from telegram.request import HTTPXRequest
except ImportError:
    print("Installing python-telegram-bot...")
    os.system("pip install python-telegram-bot==20.7")
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
    from telegram.request import HTTPXRequest

# ============ CONFIG ============
BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPER_ADMIN_ID = 6551617050
ADMIN_IDS = [SUPER_ADMIN_ID]
MAX_OTP_ATTEMPTS = 3
MAX_REGISTRATIONS_PER_SESSION = 10

# ============ LOGGING ============
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============ USER DATA MANAGER ============
class UserDataManager:
    def __init__(self):
        self.data_file = "user_data.json"
        self.lock = threading.Lock()
        self._load_data()
    
    def _load_data(self):
        try:
            if os.path.exists(self.data_file):
                with open(self.data_file, 'r') as f:
                    self.user_data = json.load(f)
            else:
                self.user_data = {}
        except:
            self.user_data = {}
    
    def _save_data(self):
        with self.lock:
            try:
                with open(self.data_file, 'w') as f:
                    json.dump(self.user_data, f, indent=2, default=str)
            except Exception as e:
                logger.error(f"Error saving data: {e}")
    
    def get_user(self, user_id: int) -> Dict:
        return self.user_data.get(str(user_id), {})
    
    def get_user_stats(self, user_id: int) -> Dict:
        user = self.get_user(user_id)
        if not user:
            return {}
        
        return {
            "total": user.get("registrations", {}).get("total", 0),
            "successful": user.get("registrations", {}).get("successful", 0),
            "failed": user.get("registrations", {}).get("failed", 0),
            "pending": user.get("registrations", {}).get("pending", 0),
            "numbers_registered": user.get("numbers_registered", []),
            "failed_numbers": user.get("failed_numbers", [])
        }
    
    def create_user(self, user_id: int, username: str = None, first_name: str = None):
        user_id_str = str(user_id)
        if user_id_str not in self.user_data:
            self.user_data[user_id_str] = {
                "user_id": user_id,
                "username": username,
                "first_name": first_name,
                "is_admin": user_id in ADMIN_IDS,
                "joined_date": datetime.now().isoformat(),
                "registrations": {"total": 0, "successful": 0, "failed": 0, "pending": 0},
                "numbers_registered": [],
                "failed_numbers": [],
                "pending_numbers": [],
                "last_activity": datetime.now().isoformat()
            }
            self._save_data()
        return self.user_data[user_id_str]
    
    def update_user_registration(self, user_id: int, phone: str, success: bool):
        """Update user registration stats - only count if number is NEW"""
        user_id_str = str(user_id)
        if user_id_str not in self.user_data:
            return
        
        user = self.user_data[user_id_str]
        
        # Check if number is already registered
        if phone in user.get("numbers_registered", []):
            # Number already registered - don't count again
            logger.info(f"Number {phone} already registered for user {user_id}, not counting again")
            if phone in user.get("pending_numbers", []):
                user["pending_numbers"].remove(phone)
            self._save_data()
            return
        
        # Number is new - count it
        if success:
            user["registrations"]["total"] += 1
            user["registrations"]["successful"] += 1
            user["numbers_registered"].append(phone)
            if phone in user.get("pending_numbers", []):
                user["pending_numbers"].remove(phone)
        else:
            user["registrations"]["total"] += 1
            user["registrations"]["failed"] += 1
            if phone not in user.get("failed_numbers", []):
                user["failed_numbers"].append(phone)
            if phone in user.get("pending_numbers", []):
                user["pending_numbers"].remove(phone)
        
        user["last_activity"] = datetime.now().isoformat()
        self._save_data()
        logger.info(f"Updated registration for {user_id}: {phone} - {'success' if success else 'failed'}")
    
    def add_pending_otp(self, user_id: int, phone: str):
        user_id_str = str(user_id)
        if user_id_str not in self.user_data:
            return
        
        user = self.user_data[user_id_str]
        if phone not in user.get("pending_numbers", []) and phone not in user.get("numbers_registered", []):
            user["pending_numbers"].append(phone)
            user["registrations"]["pending"] += 1
            self._save_data()
    
    def is_number_registered(self, user_id: int, phone: str) -> bool:
        user = self.get_user(user_id)
        return phone in user.get("numbers_registered", [])
    
    def get_registered_numbers(self, user_id: int) -> List[str]:
        user = self.get_user(user_id)
        return user.get("numbers_registered", [])
    
    def search_user(self, search_term: str) -> List[Dict]:
        search_term = search_term.lower().strip()
        results = []
        
        with self.lock:
            for user_id_str, user_data in self.user_data.items():
                if search_term in user_id_str:
                    results.append(user_data)
                    continue
                
                username = user_data.get('username', '').lower()
                if username and search_term in username:
                    results.append(user_data)
                    continue
                
                first_name = user_data.get('first_name', '').lower()
                if first_name and search_term in first_name:
                    results.append(user_data)
                    continue
                
                for num in user_data.get('numbers_registered', []):
                    if search_term in num:
                        results.append(user_data)
                        break
        
        return results
    
    def get_all_users(self) -> List[Dict]:
        with self.lock:
            return list(self.user_data.values())
    
    def get_global_stats(self) -> Dict:
        with self.lock:
            total_users = len(self.user_data)
            total_regs = 0
            successful = 0
            failed = 0
            pending = 0
            
            for user in self.user_data.values():
                regs = user.get('registrations', {})
                total_regs += regs.get('total', 0)
                successful += regs.get('successful', 0)
                failed += regs.get('failed', 0)
                pending += regs.get('pending', 0)
            
            return {
                "total_users": total_users,
                "total_registrations": total_regs,
                "successful_registrations": successful,
                "failed_registrations": failed,
                "pending_otps": pending,
                "success_rate": round((successful / max(1, total_regs)) * 100, 2) if total_regs > 0 else 0,
                "last_updated": datetime.now().isoformat()
            }
    
    def is_admin(self, user_id: int) -> bool:
        return user_id in ADMIN_IDS


# ============ CAMP INR AUTOMATION ============
class CampINRAutomation:
    def __init__(self):
        self.base_url = "https://offers.inrflash.com"
        self.session = requests.Session()
        self.ref = "XM4R"
        self.camp = "campinr"
        self.wallet_numbers = ["9616027396"]
        self.current_wallet_index = 0
        self.query_param = ""
        self.t_value = ""
        self.session_data = {}
        self.q_params = {}
        self.otp_q_params = {}
        
        self.user_agents = [
            "Mozilla/5.0 (Linux; Android 16; 25053PC47I Build/BP2A.250605.031.A3; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/149.0.7827.163 Mobile Safari/537.36",
            "Mozilla/5.0 (Linux; Android 14; SM-G998B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Mobile Safari/537.36",
            "Mozilla/5.0 (Linux; Android 13; SM-S908B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/113.0.0.0 Mobile Safari/537.36",
        ]
        
        self.headers_base = {
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "accept-encoding": "gzip, deflate, br",
            "accept-language": "en-IN,en-US;q=0.9,en;q=0.8",
            "origin": "https://offers.inrflash.com",
            "sec-ch-ua-mobile": "?1",
            "sec-ch-ua-platform": '"Android"',
            "upgrade-insecure-requests": "1",
            "x-requested-with": "mark.via.gp"
        }
        
        self._load_wallet_numbers()
    
    def _load_wallet_numbers(self):
        if os.path.exists('wallet_numbers.txt'):
            try:
                with open('wallet_numbers.txt', 'r') as f:
                    for line in f:
                        if line.strip():
                            self.wallet_numbers.append(line.strip())
            except:
                pass
    
    def _get_next_wallet(self) -> str:
        wallet = self.wallet_numbers[self.current_wallet_index % len(self.wallet_numbers)]
        self.current_wallet_index += 1
        return wallet
    
    def _get_random_user_agent(self) -> str:
        return random.choice(self.user_agents)
    
    def _extract_q_params(self, html: str) -> Dict:
        q_params = {}
        q_match = re.search(r'var\s+_q\s*=\s*({[^;]+})', html)
        if q_match:
            try:
                q_str = q_match.group(1)
                q_str = re.sub(r'(\w+):', r'"\1":', q_str)
                q_str = re.sub(r'"([^"]+)"\s*:\s*"([^"]*)"', r'"\1":"\2"', q_str)
                q_str = re.sub(r',\s*}', '}', q_str)
                q_data = json.loads(q_str)
                q_params = q_data
                logger.debug(f"Extracted _q params: {list(q_params.keys())}")
            except Exception as e:
                logger.error(f"Error parsing _q: {e}")
        return q_params
    
    def _encrypt_digits(self, digits: str, q_params: Dict) -> str:
        digits = re.sub(r'\D', '', digits)
        
        tk = q_params.get('tk', '')
        pd = q_params.get('pd', '')
        ab = q_params.get('ab', '')
        _std = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'
        
        _pad = []
        for i in range(0, len(pd) - 1, 2):
            if i + 1 < len(pd):
                try:
                    _pad.append(int(pd[i:i+2], 16))
                except:
                    _pad.append(0)
        
        salt = random.randint(0, 255)
        body = [len(digits)]
        for char in digits:
            body.append(ord(char))
        
        noise = 6 + random.randint(0, 18)
        for _ in range(noise):
            body.append(random.randint(0, 255))
        
        raw = [salt]
        for k, val in enumerate(body):
            ks = (_pad[k % len(_pad)] ^ ord(tk[k % len(tk)]) ^ salt) & 0xFF
            raw.append((val ^ ks) & 0xFF)
        
        bin_data = ''.join(chr(b & 0xFF) for b in raw)
        
        try:
            b64 = __import__('base64').b64encode(bin_data.encode('latin-1')).decode('ascii')
        except:
            b64 = ''
            for i in range(0, len(raw), 3):
                chunk = raw[i:i+3]
                if len(chunk) == 1:
                    b64 += _std[(chunk[0] >> 2) & 0x3F]
                    b64 += _std[((chunk[0] & 0x03) << 4) & 0x3F]
                    b64 += '=='
                elif len(chunk) == 2:
                    b64 += _std[(chunk[0] >> 2) & 0x3F]
                    b64 += _std[((chunk[0] & 0x03) << 4) | ((chunk[1] >> 4) & 0x0F)]
                    b64 += _std[((chunk[1] & 0x0F) << 2) & 0x3F]
                    b64 += '='
                else:
                    b64 += _std[(chunk[0] >> 2) & 0x3F]
                    b64 += _std[((chunk[0] & 0x03) << 4) | ((chunk[1] >> 4) & 0x0F)]
                    b64 += _std[((chunk[1] & 0x0F) << 2) | ((chunk[2] >> 6) & 0x03)]
                    b64 += _std[chunk[2] & 0x3F]
        
        result = ''
        for char in b64:
            if char == '=':
                break
            idx = _std.find(char)
            if idx != -1 and idx < len(ab):
                result += ab[idx]
            else:
                result += char
        
        return result
    
    def start_registration(self) -> Dict:
        try:
            wallet = self._get_next_wallet()
            user_agent = self._get_random_user_agent()
            
            self.session = requests.Session()
            headers = self.headers_base.copy()
            headers["user-agent"] = user_agent
            
            url = f"{self.base_url}/camp.php"
            params = {"ref": self.ref, "camp": self.camp}
            
            fingerprint = {
                "ua": user_agent,
                "language": "en-IN",
                "platform": "Android",
                "timezone": "Asia/Calcutta"
            }
            
            data = {
                "_fp": json.dumps(fingerprint),
                "wallet_number": wallet
            }
            
            response = self.session.post(url, params=params, data=data, headers=headers, allow_redirects=False)
            
            if response.status_code == 302:
                redirect_url = response.headers.get("location")
                parsed = urlparse(redirect_url)
                query_params = parse_qs(parsed.query)
                if '' in query_params:
                    self.query_param = query_params[''][0]
                
                full_url = urljoin(url, redirect_url)
                page_response = self.session.get(full_url, headers=headers)
                
                if page_response.status_code == 200:
                    t_value = None
                    patterns = [
                        r'name="t"\s+value="([^"]+)"',
                        r'<input[^>]*name="t"[^>]*value="([^"]+)"',
                        r't=([a-f0-9]{32})',
                        r'tk["\s:]+"([a-f0-9]{32})"',
                    ]
                    
                    for pattern in patterns:
                        match = re.search(pattern, page_response.text, re.IGNORECASE)
                        if match:
                            t_value = match.group(1)
                            break
                    
                    self.q_params = self._extract_q_params(page_response.text)
                    self.session_data['page_html'] = page_response.text
                    
                    if t_value:
                        self.t_value = t_value
                    elif 'tk' in self.q_params:
                        self.t_value = self.q_params['tk']
                    else:
                        return {"success": False, "error": "Could not extract T value"}
                    
                    return {"success": True, "wallet": wallet}
            
            return {"success": False, "error": f"Unexpected status: {response.status_code}"}
            
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def send_otp(self, phone_number: str) -> Dict:
        try:
            user_agent = self._get_random_user_agent()
            headers = self.headers_base.copy()
            headers["user-agent"] = user_agent
            
            clean_phone = re.sub(r'\D', '', phone_number)
            if len(clean_phone) >= 10:
                phone_10_digit = clean_phone[-10:]
            else:
                phone_10_digit = clean_phone
            
            if not self.q_params:
                logger.warning("No q_params found, using defaults")
                self.q_params = {
                    "m": "q8df605cd8e6e19c8",
                    "tf": "q816ad8ba077294db",
                    "af": "q2fb33e8698f19086",
                    "vf": "qf072bf52ce95d1fa",
                    "tk": self.t_value or "e5a208ff6c832d99a87b21ff33f3b2f4",
                    "pd": "2cc7ddc4cb2ca59bc88b307006796339d02de81035deb39ff44b9560d0483fc9",
                    "ab": "_5M1BLfzxIAFHr672YNTmjKWlJuERiOXUGdkvbDge0ySZ4nP8VC-ha9tQsqocwp3",
                    "ok": "kfdb69c0036",
                    "ls": "k0ab666ae13",
                    "nk": "n4c573655",
                    "vk": "vf5771331"
                }
            
            encrypted_phone = self._encrypt_digits(phone_10_digit, self.q_params)
            
            from requests_toolbelt import MultipartEncoder
            
            fields = {
                self.q_params.get('m'): '1',
                self.q_params.get('tf'): self.q_params.get('tk', self.t_value),
                self.q_params.get('af'): '1',
                self.q_params.get('vf'): encrypted_phone
            }
            
            multipart_data = MultipartEncoder(fields=fields)
            headers["content-type"] = multipart_data.content_type
            headers["accept"] = "*/*"
            headers["sec-fetch-mode"] = "cors"
            headers["sec-fetch-dest"] = "empty"
            
            ajax_url = f"{self.base_url}/{self.camp}/"
            if self.query_param:
                ajax_url += f"?={self.query_param}"
            
            response = self.session.post(ajax_url, data=multipart_data, headers=headers, allow_redirects=False)
            
            if response.status_code == 200:
                try:
                    data = response.json()
                    ok_key = self.q_params.get('ok')
                    ls_key = self.q_params.get('ls')
                    
                    if data.get(ok_key) == 1 and ls_key in data:
                        return self._submit_form_with_hidden_fields(data)
                    
                except Exception as e:
                    logger.error(f"Error parsing response: {e}")
            
            return {"success": False, "error": "Failed to send OTP"}
            
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def _submit_form_with_hidden_fields(self, data: Dict) -> Dict:
        try:
            ls_key = self.q_params.get('ls')
            nk_key = self.q_params.get('nk')
            vk_key = self.q_params.get('vk')
            
            list_data = data.get(ls_key, [])
            if not list_data:
                return {"success": True, "otp_sent": True}
            
            form_data = {}
            for item in list_data:
                if nk_key in item and vk_key in item:
                    form_data[item[nk_key]] = item[vk_key]
            
            headers = self.headers_base.copy()
            headers["user-agent"] = self._get_random_user_agent()
            headers["content-type"] = "application/x-www-form-urlencoded"
            
            submit_url = f"{self.base_url}/{self.camp}/"
            if self.query_param:
                submit_url += f"?={self.query_param}"
            
            response = self.session.post(submit_url, data=form_data, headers=headers, allow_redirects=False)
            
            if response.status_code == 302:
                redirect_url = response.headers.get("location")
                
                if redirect_url.startswith('/'):
                    full_url = f"{self.base_url}{redirect_url}"
                elif redirect_url.startswith('http'):
                    full_url = redirect_url
                else:
                    full_url = f"{self.base_url}/{self.camp}/{redirect_url}"
                
                page_response = self.session.get(full_url, headers=headers)
                
                if page_response.status_code == 200:
                    self.otp_q_params = self._extract_q_params(page_response.text)
                    if not self.otp_q_params:
                        self.otp_q_params = self.q_params.copy()
                    
                    self.session_data['otp_page_url'] = full_url
                    self.session_data['otp_page_html'] = page_response.text
                    
                    # Check for already registered message
                    if "Account already exists" in page_response.text:
                        logger.info("Account already exists on server")
                        return {"success": True, "already_registered": True}
                    
                    if "Congratulations" in page_response.text:
                        return {"success": True, "congratulations": True}
                    
                    return {"success": True, "otp_sent": True}
                
                return {"success": True, "otp_sent": True}
            
            return {"success": True, "otp_sent": True}
            
        except Exception as e:
            logger.error(f"Error in form submission: {e}")
            return {"success": True, "otp_sent": True}
    
    def verify_otp(self, otp_code: str) -> Dict:
        try:
            user_agent = self._get_random_user_agent()
            headers = self.headers_base.copy()
            headers["user-agent"] = user_agent
            
            clean_otp = re.sub(r'\D', '', otp_code)
            logger.info(f"Verifying OTP: {clean_otp}")
            
            q_params = self.otp_q_params if self.otp_q_params else self.q_params
            
            if not q_params:
                logger.warning("No q_params available, using defaults")
                q_params = {
                    "m": "qcc7ab32da49b7db9",
                    "tf": "q5722f72c12be5abb",
                    "af": "q5135e1cf25893f89",
                    "vf": "q8be1c4679c7f5ab1",
                    "tk": "3820351966eabdaff316e26b2fc88bd9",
                    "pd": "a2cf3e63a10060210784811de84bcd8b36f369a12d6b58a1d405d142f5067458",
                    "ab": "Cwkimx9M4jTz-VQGl_dDZpvtNE63uYnAfb7IJRP1XKh2H5FcergOq8SUaoWBysL0",
                    "ok": "k1f217596e7",
                    "ls": "k8264d630e0",
                    "nk": "nbaba21e3",
                    "vk": "v6bcc661a"
                }
            
            encrypted_otp = self._encrypt_digits(clean_otp, q_params)
            
            from requests_toolbelt import MultipartEncoder
            
            fields = {
                q_params.get('m'): '1',
                q_params.get('tf'): q_params.get('tk'),
                q_params.get('af'): '2',
                q_params.get('vf'): encrypted_otp
            }
            
            multipart_data = MultipartEncoder(fields=fields)
            headers["content-type"] = multipart_data.content_type
            headers["accept"] = "*/*"
            headers["sec-fetch-mode"] = "cors"
            headers["sec-fetch-dest"] = "empty"
            
            ajax_url = f"{self.base_url}/{self.camp}/index.php"
            if self.query_param:
                ajax_url += f"?={self.query_param}"
            
            response = self.session.post(ajax_url, data=multipart_data, headers=headers, allow_redirects=False)
            
            if response.status_code == 200:
                try:
                    data = response.json()
                    ok_key = q_params.get('ok')
                    ls_key = q_params.get('ls')
                    
                    if data.get(ok_key) == 1:
                        logger.info("OTP verification successful!")
                        return self._submit_otp_form(data, q_params)
                    
                except Exception as e:
                    logger.error(f"Error parsing response: {e}")
                    if '"ok":1' in response.text or f'"{q_params.get("ok")}":1' in response.text:
                        logger.info("OTP verification successful (text check)!")
                        return {"success": True, "message": "OTP verified!"}
            
            return {"success": False, "error": "OTP verification failed"}
            
        except Exception as e:
            logger.error(f"Error verifying OTP: {e}")
            return {"success": False, "error": str(e)}
    
    def _submit_otp_form(self, data: Dict, q_params: Dict) -> Dict:
        try:
            ls_key = q_params.get('ls')
            nk_key = q_params.get('nk')
            vk_key = q_params.get('vk')
            
            list_data = data.get(ls_key, [])
            if not list_data:
                return {"success": True, "message": "OTP verified!"}
            
            form_data = {}
            for item in list_data:
                if nk_key in item and vk_key in item:
                    form_data[item[nk_key]] = item[vk_key]
            
            headers = self.headers_base.copy()
            headers["user-agent"] = self._get_random_user_agent()
            headers["content-type"] = "application/x-www-form-urlencoded"
            
            submit_url = f"{self.base_url}/{self.camp}/index.php"
            if self.query_param:
                submit_url += f"?={self.query_param}"
            
            response = self.session.post(submit_url, data=form_data, headers=headers, allow_redirects=False)
            
            if response.status_code == 302:
                redirect_url = response.headers.get("location")
                
                if redirect_url.startswith('/'):
                    full_url = f"{self.base_url}{redirect_url}"
                elif redirect_url.startswith('http'):
                    full_url = redirect_url
                else:
                    full_url = f"{self.base_url}/{self.camp}/{redirect_url}"
                
                page_response = self.session.get(full_url, headers=headers)
                
                if page_response.status_code == 200:
                    page_text = page_response.text
                    
                    # Check for already registered message
                    if "Account already exists" in page_text:
                        logger.info("Account already exists on server")
                        return {"success": True, "already_registered": True}
                    
                    if "Congratulations" in page_text:
                        return {"success": True, "message": "Registration completed!"}
                    
                    if "invalid" in page_text.lower() or "wrong" in page_text.lower():
                        return {"success": False, "error": "Invalid OTP"}
            
            return {"success": True, "message": "OTP verified!"}
            
        except Exception as e:
            logger.error(f"Error in OTP form submission: {e}")
            return {"success": True, "message": "OTP verified!"}


# ============ TELEGRAM BOT ============
class TelegramBot:
    def __init__(self, token: str):
        self.token = token
        self.application = None
        self.user_manager = UserDataManager()
        self.automation = None
        self.pending_registrations = {}
        self.search_states = {}
        self.broadcast_states = {}
        self.session_registrations = {}
    
    def get_bot_application(self):
        try:
            request = HTTPXRequest(
                connection_pool_size=8,
                connect_timeout=60.0,
                read_timeout=60.0,
            )
            
            application = Application.builder() \
                .token(self.token) \
                .request(request) \
                .build()
            
            return application
        except Exception as e:
            logger.error(f"Failed to create bot application: {e}")
            raise
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        
        self.user_manager.create_user(
            user.id,
            username=user.username,
            first_name=user.first_name
        )
        
        welcome_text = f"""
🌟 *Welcome to Camp INR Registration Bot!* 🌟

Hello {user.first_name}! 👋

📱 *Commands:*
/start - Show this message
/register - Start registration session
/status - Check your status
/stats - View your statistics
/help - Show help
/cancel - Cancel registration

🔐 *Admin Commands:*
/adminstats - Global statistics
/adminusers - List users
/adminsearch - Search users
/adminbroadcast - Send broadcast

✅ *Features:*
• 🔒 Secure registration
• ⚡ Auto OTP verification
• 📊 Track your registrations
• 📈 View statistics
• 🔄 Automatic next registration
• ❌ 3 OTP retry attempts
"""
        
        keyboard = [
            [
                InlineKeyboardButton("📱 Register", callback_data="register"),
                InlineKeyboardButton("📊 My Stats", callback_data="stats")
            ],
            [
                InlineKeyboardButton("📋 Status", callback_data="status"),
                InlineKeyboardButton("❓ Help", callback_data="help")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            welcome_text,
            parse_mode='Markdown',
            reply_markup=reply_markup
        )
    
    async def help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        help_text = """
🤖 *Camp INR Registration Bot Help*

*How to Register:*
1️⃣ Send /register to start
2️⃣ Enter phone number (10 digits)
3️⃣ Wait for OTP
4️⃣ Enter OTP (6 digits) - *3 attempts allowed*
5️⃣ Registration completes

*After successful registration:*
• Bot will automatically ask for next number
• No need to type /register again
• Type /cancel to stop the session

*OTP Retry:*
• You get 3 attempts to enter correct OTP
• After 3 failed attempts, registration fails
• Use /register to start over

*Commands:*
/register - Start registration session
/cancel - Cancel current registration
/status - Check your status
/stats - View statistics
"""
        
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="back_main")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(help_text, parse_mode='Markdown', reply_markup=reply_markup)
    
    async def register(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /register command - Start new registration session"""
        user_id = update.effective_user.id
        
        # Check if there's a pending OTP verification
        if user_id in self.pending_registrations:
            pending = self.pending_registrations[user_id]
            if pending.get("state") == "waiting_otp":
                remaining = MAX_OTP_ATTEMPTS - pending.get("otp_attempts", 0)
                if remaining > 0:
                    await update.message.reply_text(
                        f"⚠️ *You have a pending OTP verification!*\n\n"
                        f"📱 Phone: `{pending.get('phone')}`\n"
                        f"⏳ Please enter the OTP\n"
                        f"📊 Attempts: {pending.get('otp_attempts', 0)}/{MAX_OTP_ATTEMPTS}\n"
                        f"📌 Remaining attempts: {remaining}\n\n"
                        f"Type /cancel to cancel.",
                        parse_mode='Markdown'
                    )
                    return
                else:
                    # Max attempts reached, clean up
                    del self.pending_registrations[user_id]
        
        # Initialize or reset session
        if user_id not in self.session_registrations:
            self.session_registrations[user_id] = {
                "active": True,
                "count": 0
            }
        else:
            # Reactivate session if it was inactive
            self.session_registrations[user_id]["active"] = True
        
        # Check if session limit reached
        if self.session_registrations[user_id]["count"] >= MAX_REGISTRATIONS_PER_SESSION:
            await update.message.reply_text(
                f"✅ *Session limit reached!*\n\n"
                f"You've completed {MAX_REGISTRATIONS_PER_SESSION} registrations in this session.\n"
                f"Send /register again to start a new session.",
                parse_mode='Markdown'
            )
            self.session_registrations[user_id]["active"] = False
            return
        
        # Get total registered numbers count
        registered_numbers = self.user_manager.get_registered_numbers(user_id)
        
        # Start new registration
        self.pending_registrations[user_id] = {
            "state": "waiting_phone",
            "phone": None,
            "otp_attempts": 0
        }
        
        await update.message.reply_text(
            f"📱 *Phone Number Registration*\n\n"
            f"📊 Your Total Registrations: {len(registered_numbers)}\n"
            f"📊 Session Progress: {self.session_registrations[user_id]['count']}/{MAX_REGISTRATIONS_PER_SESSION}\n"
            f"📝 Please enter the phone number (10 digits):\n\n"
            f"Example: `9876543210`\n\n"
            f"Type /cancel to cancel the session.",
            parse_mode='Markdown'
        )
    
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle text messages"""
        user_id = update.effective_user.id
        message = update.message.text.strip()
        
        # Skip commands
        if message.startswith('/'):
            return
        
        # Check for admin search state
        if user_id in self.search_states:
            if self.user_manager.is_admin(user_id):
                search_term = message
                await self._perform_search(update, context, search_term)
                del self.search_states[user_id]
            return
        
        # Check for admin broadcast state
        if user_id in self.broadcast_states:
            if self.user_manager.is_admin(user_id):
                broadcast_msg = message
                await self._perform_broadcast(update, context, broadcast_msg)
                del self.broadcast_states[user_id]
            return
        
        # Check if session is active
        if user_id not in self.session_registrations:
            await update.message.reply_text(
                "❌ *No active registration session!*\n\n"
                "Send /register to start a new session.",
                parse_mode='Markdown'
            )
            return
        
        if not self.session_registrations[user_id].get("active", False):
            await update.message.reply_text(
                "❌ *Session is inactive!*\n\n"
                "Send /register to start a new session.",
                parse_mode='Markdown'
            )
            return
        
        # Check if session limit reached
        if self.session_registrations[user_id]["count"] >= MAX_REGISTRATIONS_PER_SESSION:
            await update.message.reply_text(
                f"✅ *Session limit reached!*\n\n"
                f"You've completed {MAX_REGISTRATIONS_PER_SESSION} registrations.\n"
                f"Send /register to start a new session.",
                parse_mode='Markdown'
            )
            self.session_registrations[user_id]["active"] = False
            return
        
        # Check for pending registration
        if user_id not in self.pending_registrations:
            # Create new pending registration
            self.pending_registrations[user_id] = {
                "state": "waiting_phone",
                "phone": None,
                "otp_attempts": 0
            }
            await update.message.reply_text(
                "📱 *Phone Number Required*\n\n"
                "Please enter the phone number to register (10 digits):",
                parse_mode='Markdown'
            )
            return
        
        pending = self.pending_registrations[user_id]
        state = pending.get("state")
        
        if state == "waiting_phone":
            # Validate phone number
            phone = self.clean_mobile_number(message)
            if not phone:
                await update.message.reply_text(
                    "❌ *Invalid phone number!*\n\n"
                    "Please enter a valid 10-digit number.\n"
                    "📝 Example: 9876543210",
                    parse_mode='Markdown'
                )
                return
            
            # Check if already registered
            if self.user_manager.is_number_registered(user_id, phone):
                registered_numbers = self.user_manager.get_registered_numbers(user_id)
                await update.message.reply_text(
                    f"✅ *Number {phone} is already registered!*\n\n"
                    f"📊 Your Stats:\n"
                    f"✅ Successful: {len(registered_numbers)}\n"
                    f"📱 Registered Numbers: {', '.join(registered_numbers[-5:])}\n\n"
                    f"Please send a different number or /cancel to stop.",
                    parse_mode='Markdown'
                )
                # Clear pending but keep session active
                if user_id in self.pending_registrations:
                    del self.pending_registrations[user_id]
                return
            
            pending["phone"] = phone
            pending["state"] = "processing"
            
            await update.message.reply_text(
                f"📱 *Registration Started*\n\n"
                f"Phone: `{phone}`\n"
                f"⏳ Sending OTP...",
                parse_mode='Markdown'
            )
            
            await self.process_registration(user_id, phone, update, context)
        
        elif state == "waiting_otp":
            # Validate OTP
            otp = re.sub(r'\D', '', message)
            if len(otp) != 6:
                pending["otp_attempts"] = pending.get("otp_attempts", 0) + 1
                remaining = MAX_OTP_ATTEMPTS - pending["otp_attempts"]
                
                if pending["otp_attempts"] >= MAX_OTP_ATTEMPTS:
                    await update.message.reply_text(
                        f"❌ *Too many invalid attempts!*\n\n"
                        f"Registration cancelled for `{pending.get('phone')}`.\n"
                        f"Send /register to try again.",
                        parse_mode='Markdown'
                    )
                    if not self.user_manager.is_number_registered(user_id, pending.get('phone')):
                        self.user_manager.update_user_registration(user_id, pending.get('phone'), False)
                    if user_id in self.pending_registrations:
                        del self.pending_registrations[user_id]
                    return
                
                await update.message.reply_text(
                    f"❌ *Invalid OTP!*\n\n"
                    f"📌 Please enter 6 digits only.\n"
                    f"📊 Attempts: {pending['otp_attempts']}/{MAX_OTP_ATTEMPTS}\n"
                    f"📌 Remaining attempts: {remaining}\n\n"
                    f"Please try again:",
                    parse_mode='Markdown'
                )
                return
            
            # Verify OTP
            await self.verify_otp(user_id, otp, update, context)
    
    async def process_registration(self, user_id: int, phone: str, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Process registration"""
        try:
            # Double check if number is already registered (prevent race condition)
            if self.user_manager.is_number_registered(user_id, phone):
                registered_numbers = self.user_manager.get_registered_numbers(user_id)
                await update.message.reply_text(
                    f"✅ *Number {phone} is already registered!*\n\n"
                    f"📊 Your Stats:\n"
                    f"✅ Successful: {len(registered_numbers)}\n\n"
                    f"Send a different number or /cancel to stop.",
                    parse_mode='Markdown'
                )
                if user_id in self.pending_registrations:
                    del self.pending_registrations[user_id]
                return
            
            if not self.automation:
                self.automation = CampINRAutomation()
            
            # Step 1: Start registration
            start_result = self.automation.start_registration()
            
            if not start_result.get("success"):
                await update.message.reply_text(
                    f"❌ *Registration Failed*\n\n"
                    f"Error: {start_result.get('error', 'Unknown error')}\n"
                    f"Send /register to try again.",
                    parse_mode='Markdown'
                )
                if not self.user_manager.is_number_registered(user_id, phone):
                    self.user_manager.update_user_registration(user_id, phone, False)
                if user_id in self.pending_registrations:
                    del self.pending_registrations[user_id]
                return
            
            # Step 2: Send OTP
            otp_result = self.automation.send_otp(phone)
            
            if otp_result.get("already_registered"):
                # Account already exists on server
                await update.message.reply_text(
                    f"⚠️ *Number {phone} is already registered on the server!*\n\n"
                    f"📱 Phone: `{phone}`\n"
                    f"❌ This number cannot be registered again.\n\n"
                    f"Please send a different number or /cancel to stop.",
                    parse_mode='Markdown'
                )
                # Don't count as successful - it's already registered
                # Just clear pending state
                if user_id in self.pending_registrations:
                    del self.pending_registrations[user_id]
                return
            
            if otp_result.get("congratulations"):
                # New registration successful
                if not self.user_manager.is_number_registered(user_id, phone):
                    self.user_manager.update_user_registration(user_id, phone, True)
                    
                    # Update session count
                    if user_id in self.session_registrations:
                        self.session_registrations[user_id]["count"] += 1
                    
                    await update.message.reply_text(
                        f"🎉 *Registration Successful!*\n\n"
                        f"📱 Phone: `{phone}`\n"
                        f"✅ Status: Registered\n\n"
                        f"📊 Session Progress: {self.session_registrations.get(user_id, {}).get('count', 0)}/{MAX_REGISTRATIONS_PER_SESSION}\n\n"
                        f"Send another number or /cancel to stop.",
                        parse_mode='Markdown'
                    )
                    
                    # Show stats
                    registered_numbers = self.user_manager.get_registered_numbers(user_id)
                    await update.message.reply_text(
                        f"📊 *Your Stats*\n\n"
                        f"✅ Successful: {len(registered_numbers)}\n"
                        f"📱 Registered Numbers: {', '.join(registered_numbers[-5:])}",
                        parse_mode='Markdown'
                    )
                    
                    # Clear pending and start next registration automatically
                    if user_id in self.pending_registrations:
                        del self.pending_registrations[user_id]
                    
                    # Start next registration automatically if session is active
                    if user_id in self.session_registrations and self.session_registrations[user_id].get("active", False):
                        if self.session_registrations[user_id]["count"] < MAX_REGISTRATIONS_PER_SESSION:
                            # Start new registration
                            self.pending_registrations[user_id] = {
                                "state": "waiting_phone",
                                "phone": None,
                                "otp_attempts": 0
                            }
                            await update.message.reply_text(
                                f"🔄 *Next Registration Ready*\n\n"
                                f"📊 Progress: {self.session_registrations[user_id]['count']}/{MAX_REGISTRATIONS_PER_SESSION}\n"
                                f"📝 Send the next phone number or /cancel to stop.",
                                parse_mode='Markdown'
                            )
                        else:
                            await update.message.reply_text(
                                f"✅ *Session Complete!*\n\n"
                                f"You've completed {MAX_REGISTRATIONS_PER_SESSION} registrations.\n"
                                f"Send /register to start a new session.",
                                parse_mode='Markdown'
                            )
                            self.session_registrations[user_id]["active"] = False
                else:
                    await update.message.reply_text(
                        f"✅ *Number {phone} is already registered!*",
                        parse_mode='Markdown'
                    )
                    if user_id in self.pending_registrations:
                        del self.pending_registrations[user_id]
                return
            
            if not otp_result.get("success"):
                await update.message.reply_text(
                    f"❌ *OTP Sending Failed*\n\n"
                    f"Error: {otp_result.get('error', 'Unknown error')}\n"
                    f"Send /register to try again.",
                    parse_mode='Markdown'
                )
                if not self.user_manager.is_number_registered(user_id, phone):
                    self.user_manager.update_user_registration(user_id, phone, False)
                if user_id in self.pending_registrations:
                    del self.pending_registrations[user_id]
                return
            
            # Update state to waiting for OTP
            pending = self.pending_registrations.get(user_id, {})
            pending["state"] = "waiting_otp"
            pending["otp_attempts"] = 0
            self.pending_registrations[user_id] = pending
            
            self.user_manager.add_pending_otp(user_id, phone)
            
            await update.message.reply_text(
                f"✅ *OTP Sent Successfully!*\n\n"
                f"📱 Phone: `{phone}`\n"
                f"📌 Please enter the 6-digit OTP.\n"
                f"ℹ️ You have {MAX_OTP_ATTEMPTS} attempts.",
                parse_mode='Markdown'
            )
            
        except Exception as e:
            logger.error(f"Error in process_registration: {e}")
            await update.message.reply_text(
                f"❌ *Registration Error*\n\n"
                f"An unexpected error occurred.\n"
                f"Please try again.",
                parse_mode='Markdown'
            )
            if not self.user_manager.is_number_registered(user_id, phone):
                self.user_manager.update_user_registration(user_id, phone, False)
            if user_id in self.pending_registrations:
                del self.pending_registrations[user_id]
    
    async def verify_otp(self, user_id: int, otp: str, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Verify OTP"""
        pending = self.pending_registrations.get(user_id)
        if not pending:
            await update.message.reply_text("❌ No pending registration.")
            return
        
        phone = pending.get("phone")
        
        try:
            # Double check if number is already registered
            if self.user_manager.is_number_registered(user_id, phone):
                registered_numbers = self.user_manager.get_registered_numbers(user_id)
                await update.message.reply_text(
                    f"✅ *Number {phone} is already registered!*\n\n"
                    f"📊 Your Stats:\n"
                    f"✅ Successful: {len(registered_numbers)}\n\n"
                    f"Send a different number or /cancel to stop.",
                    parse_mode='Markdown'
                )
                if user_id in self.pending_registrations:
                    del self.pending_registrations[user_id]
                return
            
            if not self.automation:
                self.automation = CampINRAutomation()
            
            # Verify OTP
            verify_result = self.automation.verify_otp(otp)
            
            # Check if account already exists
            if verify_result.get("already_registered"):
                await update.message.reply_text(
                    f"⚠️ *Number {phone} is already registered on the server!*\n\n"
                    f"📱 Phone: `{phone}`\n"
                    f"❌ This number cannot be registered again.\n\n"
                    f"Please send a different number or /cancel to stop.",
                    parse_mode='Markdown'
                )
                # Don't count as successful
                if user_id in self.pending_registrations:
                    del self.pending_registrations[user_id]
                return
            
            if verify_result.get("success"):
                # Only count if number is NOT already registered
                if not self.user_manager.is_number_registered(user_id, phone):
                    self.user_manager.update_user_registration(user_id, phone, True)
                    
                    # Update session count
                    if user_id in self.session_registrations:
                        self.session_registrations[user_id]["count"] += 1
                    
                    await update.message.reply_text(
                        f"🎉 *Registration Successful!*\n\n"
                        f"📱 Phone: `{phone}`\n"
                        f"✅ Status: Registered\n\n"
                        f"📊 Session Progress: {self.session_registrations.get(user_id, {}).get('count', 0)}/{MAX_REGISTRATIONS_PER_SESSION}\n\n"
                        f"Send another number or /cancel to stop.",
                        parse_mode='Markdown'
                    )
                    
                    # Show stats
                    registered_numbers = self.user_manager.get_registered_numbers(user_id)
                    await update.message.reply_text(
                        f"📊 *Your Stats*\n\n"
                        f"✅ Successful: {len(registered_numbers)}\n"
                        f"📱 Registered Numbers: {', '.join(registered_numbers[-5:])}",
                        parse_mode='Markdown'
                    )
                    
                    # Clear pending and start next registration automatically
                    if user_id in self.pending_registrations:
                        del self.pending_registrations[user_id]
                    
                    # Start next registration automatically if session is active
                    if user_id in self.session_registrations and self.session_registrations[user_id].get("active", False):
                        if self.session_registrations[user_id]["count"] < MAX_REGISTRATIONS_PER_SESSION:
                            # Start new registration
                            self.pending_registrations[user_id] = {
                                "state": "waiting_phone",
                                "phone": None,
                                "otp_attempts": 0
                            }
                            await update.message.reply_text(
                                f"🔄 *Next Registration Ready*\n\n"
                                f"📊 Progress: {self.session_registrations[user_id]['count']}/{MAX_REGISTRATIONS_PER_SESSION}\n"
                                f"📝 Send the next phone number or /cancel to stop.",
                                parse_mode='Markdown'
                            )
                        else:
                            await update.message.reply_text(
                                f"✅ *Session Complete!*\n\n"
                                f"You've completed {MAX_REGISTRATIONS_PER_SESSION} registrations.\n"
                                f"Send /register to start a new session.",
                                parse_mode='Markdown'
                            )
                            self.session_registrations[user_id]["active"] = False
                else:
                    await update.message.reply_text(
                        f"✅ *Number {phone} is already registered!*",
                        parse_mode='Markdown'
                    )
                    if user_id in self.pending_registrations:
                        del self.pending_registrations[user_id]
                
            else:
                # OTP verification failed
                pending["otp_attempts"] = pending.get("otp_attempts", 0) + 1
                remaining = MAX_OTP_ATTEMPTS - pending["otp_attempts"]
                
                if remaining > 0:
                    await update.message.reply_text(
                        f"❌ *Invalid OTP!*\n\n"
                        f"📊 Attempt: {pending['otp_attempts']}/{MAX_OTP_ATTEMPTS}\n"
                        f"📌 Remaining attempts: {remaining}\n\n"
                        f"Please enter the correct 6-digit OTP:",
                        parse_mode='Markdown'
                    )
                else:
                    await update.message.reply_text(
                        f"❌ *OTP Verification Failed - Max attempts reached!*\n\n"
                        f"📱 Phone: `{phone}`\n"
                        f"Registration cancelled.\n"
                        f"Send /register to try again.",
                        parse_mode='Markdown'
                    )
                    if not self.user_manager.is_number_registered(user_id, phone):
                        self.user_manager.update_user_registration(user_id, phone, False)
                    if user_id in self.pending_registrations:
                        del self.pending_registrations[user_id]
            
        except Exception as e:
            logger.error(f"Error in verify_otp: {e}")
            await update.message.reply_text(
                f"❌ *OTP Verification Error*\n\n"
                f"Please try again.",
                parse_mode='Markdown'
            )
            if not self.user_manager.is_number_registered(user_id, phone):
                self.user_manager.update_user_registration(user_id, phone, False)
            if user_id in self.pending_registrations:
                del self.pending_registrations[user_id]
    
    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /status command"""
        user_id = update.effective_user.id
        user_data = self.user_manager.get_user(user_id)
        pending = self.pending_registrations.get(user_id)
        
        if not user_data:
            await update.message.reply_text("❌ No user data found. Use /start to initialize.")
            return
        
        registered_numbers = self.user_manager.get_registered_numbers(user_id)
        registrations = user_data.get('registrations', {})
        
        status_text = f"""
📱 *Your Registration Status*

👤 User: {user_data.get('first_name', 'Unknown')}
📊 Total Registrations: {len(registered_numbers)}
✅ Successful: {registrations.get('successful', 0)}
❌ Failed: {registrations.get('failed', 0)}
"""
        
        if user_id in self.session_registrations:
            session = self.session_registrations[user_id]
            status_text += f"\n🔄 Session Progress: {session.get('count', 0)}/{MAX_REGISTRATIONS_PER_SESSION}"
            if not session.get('active', False):
                status_text += " (Inactive)"
            else:
                status_text += " (Active)"
        
        if pending:
            status_text += f"\n*Active Registration:*\n📱 Phone: {pending.get('phone', 'N/A')}\n"
            if pending.get('state') == 'waiting_otp':
                status_text += f"⏳ Waiting for OTP (Attempt {pending.get('otp_attempts', 0)}/{MAX_OTP_ATTEMPTS})"
        
        if registered_numbers:
            status_text += f"\n\n*Registered Numbers:*\n"
            for num in registered_numbers[-5:]:
                status_text += f"• `{num}`\n"
        
        await update.message.reply_text(status_text, parse_mode='Markdown')
    
    async def stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /stats command"""
        user_id = update.effective_user.id
        user_data = self.user_manager.get_user(user_id)
        
        if not user_data:
            await update.message.reply_text("❌ No statistics found.")
            return
        
        registered_numbers = self.user_manager.get_registered_numbers(user_id)
        registrations = user_data.get('registrations', {})
        
        stats_text = f"""
📊 *Your Registration Statistics*

📈 Total Successful: {len(registered_numbers)}
✅ Successful Count: {registrations.get('successful', 0)}
❌ Failed: {registrations.get('failed', 0)}
📱 Registered Numbers: {len(registered_numbers)}
"""
        
        if user_id in self.session_registrations:
            session = self.session_registrations[user_id]
            stats_text += f"\n🔄 Current Session: {session.get('count', 0)}/{MAX_REGISTRATIONS_PER_SESSION}"
        
        if registered_numbers:
            stats_text += f"\n\n*Registered Numbers:*\n"
            for num in registered_numbers[-10:]:
                stats_text += f"• `{num}`\n"
        
        keyboard = [[InlineKeyboardButton("🔄 Refresh", callback_data="refresh_stats")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(stats_text, parse_mode='Markdown', reply_markup=reply_markup)
    
    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /cancel command"""
        user_id = update.effective_user.id
        
        # Cancel active session
        if user_id in self.session_registrations:
            self.session_registrations[user_id]["active"] = False
        
        if user_id in self.pending_registrations:
            phone = self.pending_registrations[user_id].get('phone')
            del self.pending_registrations[user_id]
            await update.message.reply_text(
                f"✅ *Registration cancelled!*\n\n"
                f"📱 Phone: `{phone if phone else 'N/A'}`\n"
                f"Use /register to start a new session.",
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text(
                "❌ No active registration to cancel.\n\n"
                "Use /register to start a new session.",
                parse_mode='Markdown'
            )
    
    # ============ ADMIN COMMANDS ============
    
    async def admin_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        
        if not self.user_manager.is_admin(user_id):
            await update.message.reply_text("⛔ *Unauthorized!* You are not an admin.", parse_mode='Markdown')
            return
        
        stats = self.user_manager.get_global_stats()
        all_users = self.user_manager.get_all_users()
        
        stats_text = f"""
📊 *Global Statistics*

👥 Total Users: {stats.get('total_users', 0)}
📱 Total Registrations: {stats.get('total_registrations', 0)}
✅ Successful: {stats.get('successful_registrations', 0)}
❌ Failed: {stats.get('failed_registrations', 0)}
⏳ Pending OTPs: {stats.get('pending_otps', 0)}
📈 Success Rate: {stats.get('success_rate', 0)}%
🕐 Last Updated: {stats.get('last_updated', 'N/A')}

*Recent Users:*\n"""
        
        for user in all_users[-10:]:
            regs = user.get('registrations', {})
            registered = user.get('numbers_registered', [])
            stats_text += f"• {user.get('first_name', 'Unknown')} (@{user.get('username', 'N/A')}) - "
            stats_text += f"Total: {regs.get('total', 0)}, "
            stats_text += f"Success: {regs.get('successful', 0)}, "
            stats_text += f"Failed: {regs.get('failed', 0)}, "
            stats_text += f"Unique: {len(registered)}\n"
        
        if len(all_users) > 10:
            stats_text += f"\n... and {len(all_users) - 10} more users"
        
        keyboard = [
            [
                InlineKeyboardButton("👥 View All Users", callback_data="admin_users"),
                InlineKeyboardButton("🔍 Search Users", callback_data="admin_search")
            ],
            [InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(stats_text, parse_mode='Markdown', reply_markup=reply_markup)
    
    async def admin_users(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        
        if not self.user_manager.is_admin(user_id):
            await update.message.reply_text("⛔ *Unauthorized!* You are not an admin.", parse_mode='Markdown')
            return
        
        all_users = self.user_manager.get_all_users()
        
        if not all_users:
            await update.message.reply_text("No users found.")
            return
        
        users_text = "👥 *User List*\n\n"
        for idx, user in enumerate(all_users, 1):
            regs = user.get('registrations', {})
            registered = user.get('numbers_registered', [])
            users_text += f"{idx}. {user.get('first_name', 'Unknown')} (@{user.get('username', 'N/A')})\n"
            users_text += f"   📱 Total: {regs.get('total', 0)}, ✅ Success: {regs.get('successful', 0)}, ❌ Failed: {regs.get('failed', 0)}\n"
            users_text += f"   📱 Unique Numbers: {len(registered)}\n"
            if user.get('is_admin', False):
                users_text += "   ⭐ Admin\n"
            users_text += "\n"
        
        if len(users_text) > 4000:
            chunks = [users_text[i:i+4000] for i in range(0, len(users_text), 4000)]
            for chunk in chunks:
                await update.message.reply_text(chunk, parse_mode='Markdown')
        else:
            keyboard = [
                [InlineKeyboardButton("🔍 Search Users", callback_data="admin_search")],
                [InlineKeyboardButton("🔙 Back to Stats", callback_data="admin_back")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(users_text, parse_mode='Markdown', reply_markup=reply_markup)
    
    async def admin_search(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        
        if not self.user_manager.is_admin(user_id):
            await update.message.reply_text("⛔ *Unauthorized!* You are not an admin.", parse_mode='Markdown')
            return
        
        if context.args:
            search_term = ' '.join(context.args)
            await self._perform_search(update, context, search_term)
        else:
            await update.message.reply_text(
                "🔍 *Search Users*\n\n"
                "Please send the search term (username, first name, user ID, or phone number).\n\n"
                "Example: `John` or `9876543210`",
                parse_mode='Markdown'
            )
            self.search_states[user_id] = True
    
    async def _perform_search(self, update: Update, context: ContextTypes.DEFAULT_TYPE, search_term: str):
        user_id = update.effective_user.id
        
        if not self.user_manager.is_admin(user_id):
            await update.message.reply_text("⛔ Unauthorized.")
            return
        
        results = self.user_manager.search_user(search_term)
        
        if not results:
            await update.message.reply_text(
                f"🔍 No users found matching: `{search_term}`",
                parse_mode='Markdown'
            )
            return
        
        results_text = f"🔍 *Search Results for:* `{search_term}`\n\n*Found {len(results)} users:*\n\n"
        
        for idx, user in enumerate(results[:10], 1):
            regs = user.get('registrations', {})
            registered = user.get('numbers_registered', [])
            results_text += f"{idx}. {user.get('first_name', 'Unknown')} (@{user.get('username', 'N/A')}) - ID: `{user.get('user_id')}`\n"
            results_text += f"   📱 Total: {regs.get('total', 0)}, ✅ Success: {regs.get('successful', 0)}\n"
            results_text += f"   📱 Unique Numbers: {len(registered)}\n"
            if user.get('is_admin', False):
                results_text += "   ⭐ Admin\n"
            results_text += "\n"
        
        if len(results) > 10:
            results_text += f"... and {len(results) - 10} more results"
        
        keyboard = [
            [InlineKeyboardButton("🔍 New Search", callback_data="admin_search")],
            [InlineKeyboardButton("🔙 Back to Stats", callback_data="admin_back")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(results_text, parse_mode='Markdown', reply_markup=reply_markup)
    
    async def admin_broadcast(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        
        if not self.user_manager.is_admin(user_id):
            await update.message.reply_text("⛔ *Unauthorized!* You are not an admin.", parse_mode='Markdown')
            return
        
        if context.args:
            broadcast_msg = ' '.join(context.args)
            await self._perform_broadcast(update, context, broadcast_msg)
        else:
            await update.message.reply_text(
                "📢 *Broadcast Message*\n\n"
                "Please send the message you want to broadcast to all users.\n\n"
                "To cancel, send /cancel",
                parse_mode='Markdown'
            )
            self.broadcast_states[user_id] = True
    
    async def _perform_broadcast(self, update: Update, context: ContextTypes.DEFAULT_TYPE, message: str):
        user_id = update.effective_user.id
        
        if not self.user_manager.is_admin(user_id):
            await update.message.reply_text("⛔ Unauthorized.")
            return
        
        all_users = self.user_manager.get_all_users()
        
        if not all_users:
            await update.message.reply_text("No users to broadcast to.")
            return
        
        sent_count = 0
        failed_count = 0
        
        status_msg = await update.message.reply_text(
            f"📢 Broadcasting message to {len(all_users)} users...\n"
            f"This may take a moment."
        )
        
        for idx, user in enumerate(all_users):
            try:
                await context.bot.send_message(
                    chat_id=user['user_id'],
                    text=f"📢 *Announcement*\n\n{message}",
                    parse_mode='Markdown'
                )
                sent_count += 1
                time.sleep(0.1)
                
                if idx % 10 == 0:
                    await status_msg.edit_text(
                        f"📢 Broadcasting...\n\n"
                        f"⏳ Progress: {idx}/{len(all_users)}\n"
                        f"✅ Sent: {sent_count}\n"
                        f"❌ Failed: {failed_count}"
                    )
            except Exception as e:
                logger.error(f"Failed to send to {user['user_id']}: {e}")
                failed_count += 1
        
        await status_msg.edit_text(
            f"✅ *Broadcast Complete!*\n\n"
            f"✅ Sent to: {sent_count}\n"
            f"❌ Failed: {failed_count}\n"
            f"👥 Total Users: {len(all_users)}",
            parse_mode='Markdown'
        )
    
    # ============ CALLBACK HANDLER ============
    
    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        user_id = query.from_user.id
        callback_data = query.data
        
        class FakeUpdate:
            def __init__(self, query):
                self.effective_user = query.from_user
                self.message = query.message
        
        fake_update = FakeUpdate(query)
        
        if callback_data == "register":
            await query.message.delete()
            await self.register(fake_update, context)
        
        elif callback_data == "stats":
            await query.message.delete()
            await self.stats(fake_update, context)
        
        elif callback_data == "status":
            await query.message.delete()
            await self.status(fake_update, context)
        
        elif callback_data == "help":
            await query.message.delete()
            await self.help(fake_update, context)
        
        elif callback_data == "back_main":
            await query.message.delete()
            await self.start(fake_update, context)
        
        elif callback_data == "refresh_stats":
            await query.message.delete()
            await self.stats(fake_update, context)
        
        elif callback_data == "admin_back":
            await query.message.delete()
            await self.admin_stats(fake_update, context)
        
        elif callback_data == "admin_users":
            await query.message.delete()
            await self.admin_users(fake_update, context)
        
        elif callback_data == "admin_search":
            if self.user_manager.is_admin(user_id):
                await query.message.delete()
                await query.message.reply_text(
                    "🔍 *Search Users*\n\n"
                    "Please send the search term (username, first name, user ID, or phone number).\n\n"
                    "Example: `John` or `9876543210`",
                    parse_mode='Markdown'
                )
                self.search_states[user_id] = True
            else:
                await query.message.reply_text("⛔ Unauthorized!")
        
        elif callback_data == "admin_broadcast":
            if self.user_manager.is_admin(user_id):
                await query.message.delete()
                await query.message.reply_text(
                    "📢 *Broadcast Message*\n\n"
                    "Please send the message you want to broadcast to all users.\n\n"
                    "To cancel, send /cancel",
                    parse_mode='Markdown'
                )
                self.broadcast_states[user_id] = True
            else:
                await query.message.reply_text("⛔ Unauthorized!")
    
    # ============ UTILITY FUNCTIONS ============
    
    def clean_mobile_number(self, number: str) -> Optional[str]:
        if not number:
            return None
        
        cleaned = re.sub(r'^\+?91', '', str(number))
        cleaned = re.sub(r'\D', '', cleaned)
        
        if len(cleaned) == 10 and cleaned[0] in '6789':
            return cleaned
        elif len(cleaned) > 10:
            cleaned = cleaned[-10:]
            if cleaned[0] in '6789':
                return cleaned
        
        return None
    
    # ============ RUN BOT ============
    
    def run(self):
        try:
            self.application = self.get_bot_application()
            
            self.application.add_handler(CommandHandler("start", self.start))
            self.application.add_handler(CommandHandler("help", self.help))
            self.application.add_handler(CommandHandler("register", self.register))
            self.application.add_handler(CommandHandler("status", self.status))
            self.application.add_handler(CommandHandler("stats", self.stats))
            self.application.add_handler(CommandHandler("cancel", self.cancel))
            
            self.application.add_handler(CommandHandler("adminstats", self.admin_stats))
            self.application.add_handler(CommandHandler("adminusers", self.admin_users))
            self.application.add_handler(CommandHandler("adminsearch", self.admin_search))
            self.application.add_handler(CommandHandler("adminbroadcast", self.admin_broadcast))
            
            self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
            self.application.add_handler(CallbackQueryHandler(self.button_callback))
            
            print("🤖 Camp INR Registration Bot starting...")
            print(f"Bot token: {self.token[:10]}...")
            print(f"Super Admin ID: {SUPER_ADMIN_ID}")
            print(f"Max OTP Attempts: {MAX_OTP_ATTEMPTS}")
            print(f"Max Registrations per Session: {MAX_REGISTRATIONS_PER_SESSION}")
            print("\n💡 Bot is running! Send /start to your bot.")
            print("   Press Ctrl+C to stop.\n")
            
            self.application.run_polling(allowed_updates=Update.ALL_TYPES)
            
        except Exception as e:
            logger.error(f"Failed to run bot: {e}")
            print(f"\n❌ Error: {e}")
            raise


# ============ MAIN ============
def main():
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("⚠️ Please set your BOT_TOKEN in the script!")
        return
    
    try:
        import requests_toolbelt
    except ImportError:
        print("Installing requests-toolbelt...")
        os.system("pip install requests-toolbelt")
        import requests_toolbelt
    
    bot = TelegramBot(BOT_TOKEN)
    
    try:
        bot.run()
    except KeyboardInterrupt:
        print("\n\n🛑 Bot stopped by user.")
    except Exception as e:
        print(f"\n❌ Error: {e}")


if __name__ == "__main__":
    main()