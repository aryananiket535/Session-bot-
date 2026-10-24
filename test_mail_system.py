# ==============================================================================
# Comprehensive Test Suite for Mail Multiplexer, Binding Engine & Vault
# ==============================================================================

import asyncio
import os
import sys
import tempfile
import time

import mail_engine
import session_core
import operations
import handlers

def test_permutation_generator():
    print("[1] Testing PermutationGenerator...")
    base = "asdf1234@gmail.com"
    gen = mail_engine.PermutationGenerator(base)
    
    # 4 letters -> 2^4 = 16 case variants
    # 8 clean chars -> 2^7 = 128 dot combinations
    # Total = 16 * 128 = 2048
    assert gen.total_permutations == 2048, f"Expected 2048, got {gen.total_permutations}"
    print(f" -> Total permutations for {base}: {gen.total_permutations:,}")
    
    # Verify natural priority ordering:
    # First 16 must have 0 dots (pure case variations)
    first_16 = [gen.get_permutation(i) for i in range(16)]
    for email in first_16:
        local = email.split("@")[0]
        assert "." not in local, f"Expected 0 dots in {email}"
    print(" -> First 16 permutations have 0 dots (pure case variants): OK")
    
    # Verify case sensitivity in Telegram perspective (all variants unique)
    all_perms = [gen.get_permutation(i) for i in range(gen.total_permutations)]
    assert len(set(all_perms)) == 2048, "Expected 2048 unique strings"
    print(" -> All 2,048 permutations are unique strings: OK")
    
    # Test unused tracking
    used = all_perms[:10]
    next_unused = gen.get_next_unused(used)
    assert next_unused == all_perms[10]
    assert gen.remaining_count(used) == 2048 - 10
    print(" -> Unused tracking and remaining count: OK")
    
    # Test non-Gmail (dots are preserved as-is, only case permutations)
    gen_outlook = mail_engine.PermutationGenerator("asdf1234@outlook.com")
    assert gen_outlook.total_permutations == 16, f"Expected 16, got {gen_outlook.total_permutations}"
    print(f" -> Non-Gmail (Outlook) total permutations: {gen_outlook.total_permutations}: OK")


def test_provider_detection():
    print("[2] Testing IMAP Provider Auto-Detection...")
    test_cases = [
        ("user@gmail.com", "imap.gmail.com", 993, "Gmail"),
        ("test@outlook.com", "outlook.office365.com", 993, "Outlook"),
        ("acc@yahoo.com", "imap.mail.yahoo.com", 993, "Yahoo"),
        ("box@mail.ru", "imap.mail.ru", 993, "Mail"),
        ("me@yandex.com", "imap.yandex.com", 993, "Yandex"),
        ("custom@mycorp.net", "imap.mycorp.net", 993, "mycorp.net"),
    ]
    for email, exp_host, exp_port, exp_prov in test_cases:
        p = mail_engine.detect_imap_provider(email)
        assert p["host"] == exp_host, f"Expected {exp_host}, got {p['host']}"
        assert p["port"] == exp_port, f"Expected {exp_port}, got {p['port']}"
        print(f" -> {email:20} -> {p['host']}:{p['port']} ({p['provider']})")


def test_mail_vault_isolation():
    print("[3] Testing User Mail Vault Isolation & Admin Access...")
    meta = {}
    
    # User 1 adds mail config
    user1_id = 111111
    user1_cfg = {
        "email": "user1@gmail.com",
        "app_password": "secret_app_password_1",
        "imap_host": "imap.gmail.com",
        "imap_port": 993,
        "provider": "Gmail",
        "total_permutations": 2048,
        "used_permutations": [],
        "accounts_bound": 0,
        "updated_at": int(time.time()),
    }
    mail_engine.set_user_mail_config(meta, user1_id, user1_cfg)
    
    # User 2 adds mail config
    user2_id = 222222
    user2_cfg = {
        "email": "user2@outlook.com",
        "app_password": "secret_app_password_2",
        "imap_host": "outlook.office365.com",
        "imap_port": 993,
        "provider": "Outlook",
        "total_permutations": 16,
        "used_permutations": [],
        "accounts_bound": 0,
        "updated_at": int(time.time()),
    }
    mail_engine.set_user_mail_config(meta, user2_id, user2_cfg)
    
    # Check user 1 sees ONLY user 1
    u1_read = mail_engine.get_user_mail_config(meta, user1_id)
    assert u1_read["email"] == "user1@gmail.com"
    assert u1_read["app_password"] == "secret_app_password_1"
    
    # Check user 2 sees ONLY user 2
    u2_read = mail_engine.get_user_mail_config(meta, user2_id)
    assert u2_read["email"] == "user2@outlook.com"
    assert u2_read["app_password"] == "secret_app_password_2"
    
    # Check third user has no config
    u3_read = mail_engine.get_user_mail_config(meta, 333333)
    assert u3_read == {}
    print(" -> User vault data strictly isolated per user_id: OK")
    
    # Track used permutations
    mail_engine.add_used_permutation(meta, user1_id, "Asdf1234@gmail.com")
    mail_engine.add_used_permutation(meta, user1_id, "asDf1234@gmail.com")
    used_u1 = mail_engine.get_used_permutations(meta, user1_id)
    assert len(used_u1) == 2
    assert "Asdf1234@gmail.com" in used_u1
    assert "asDf1234@gmail.com" in used_u1
    assert mail_engine.get_used_permutations(meta, user2_id) == []
    print(" -> Used permutation tracking per user: OK")
    
    # Admin can list all configs
    all_vault = mail_engine.list_all_mail_configs(meta)
    assert len(all_vault) == 2
    assert "111111" in all_vault
    assert "222222" in all_vault
    print(" -> Admin panel vault listing: OK")
    
    # Delete user 1
    mail_engine.delete_user_mail_config(meta, user1_id)
    assert mail_engine.get_user_mail_config(meta, user1_id) == {}
    assert mail_engine.get_user_mail_config(meta, user2_id)["email"] == "user2@outlook.com"
    print(" -> User config deletion isolation: OK")


def test_keyboards_and_ui():
    print("[4] Testing Session Core Keyboards & UI...")
    # operations_keyboard
    ops_kb = session_core.operations_keyboard(10)
    buttons_data = [btn.callback_data for row in ops_kb.inline_keyboard for btn in row]
    assert "zip|check_mail|10" in buttons_data, "Check mail button missing"
    assert "zip|bind_mail|10" in buttons_data, "Bind mail button missing"
    print(" -> operations_keyboard contains 'zip|check_mail|10' and 'zip|bind_mail|10': OK")
    
    # mail_binding_keyboard
    bind_kb = session_core.mail_binding_keyboard(10, has_self_mail=True)
    bind_data = [btn.callback_data for row in bind_kb.inline_keyboard for btn in row]
    assert "mailbind|temp|10" in bind_data
    assert "mailbind|self|10" in bind_data
    assert "mailbind|setup|10" in bind_data
    assert "mailbind|back|10" in bind_data
    
    bind_kb_no_self = session_core.mail_binding_keyboard(10, has_self_mail=False)
    bind_no_self_labels = [btn.text for row in bind_kb_no_self.inline_keyboard for btn in row]
    assert "📧 Self-Mail (Setup First)" in bind_no_self_labels
    print(" -> mail_binding_keyboard routes (temp, self, setup, back): OK")
    
    # mail_settings_keyboard
    settings_kb = session_core.mail_settings_keyboard(has_self_mail=True)
    settings_data = [btn.callback_data for row in settings_kb.inline_keyboard for btn in row]
    assert "mail_setup_start" in settings_data
    assert "mail_setup_test" in settings_data
    assert "mail_setup_delete" in settings_data
    print(" -> mail_settings_keyboard routes (setup, test, delete): OK")
    
    # start_keyboard
    start_kb = session_core.start_keyboard()
    start_data = [btn.callback_data for row in start_kb.inline_keyboard for btn in row if btn.callback_data]
    assert "mail_settings_open" in start_data
    print(" -> start_keyboard contains 'mail_settings_open': OK")
    
    # admin_keyboard
    admin_kb = session_core.admin_keyboard()
    admin_data = [btn.callback_data for row in admin_kb.inline_keyboard for btn in row if btn.callback_data]
    assert "admin_mail_vault" in admin_data
    print(" -> admin_keyboard contains 'admin_mail_vault': OK")


def test_code_regex_and_extractors():
    print("[5] Testing Telegram OTP Code Regex & Extractors...")
    test_bodies = [
        ("Telegram: Your login code is 849201. Never share it with anyone.", "849201"),
        ("Your verification code is 12345.", "12345"),
        ("Security code: 593021. If you didn't request this...", "593021"),
        ("<html><body><p>Telegram code: <b>782910</b></p></body></html>", "782910"),
    ]
    for text, expected_code in test_bodies:
        m = mail_engine.TempMailClient.CODE_REGEX.search(text)
        assert m, f"Failed to match code in: {text}"
        assert m.group(1) == expected_code, f"Expected {expected_code}, got {m.group(1)}"
    
    # Test enhanced extract_telegram_code
    advanced_tests = [
        ("Telegram code: 849 201", "849201"),
        ("Your login code is 123-456", "123456"),
        ("Subject: 2026-10-05 account 100124 update. Body: Telegram verification code: 654321", "654321"),
        ("Telegram code: 123&nbsp;456", "123456"),
        ("Код подтверждения: 987654", "987654"),
    ]
    for text, expected_code in advanced_tests:
        extracted = mail_engine.extract_telegram_code(text)
        assert extracted == expected_code, f"Expected {expected_code}, got {extracted} in: {text}"

    # Test Telegram-specific Subject line formats (e.g. Your Code - 271513, Arabic, etc.)
    subject_tests = [
        ("Your Code - 271513", "271513"),
        ("Your Code - 541601", "541601"),
        ("Your Code - 16925", "16925"),
        ("رمز ورود: 16401", "16401"),
        ("رمزك هو: 16401", "16401"),
        ("Your Telegram verification code is: 112233", "112233"),
        ("Your code is 654321", "654321"),
        ("Subject: Your Code - 946217", "946217"),
    ]
    for text, expected_code in subject_tests:
        extracted = mail_engine.extract_telegram_code(text)
        assert extracted == expected_code, f"Expected {expected_code}, got {extracted} in: {text}"

    print(" -> Telegram OTP regex & extract_telegram_code extract 5-6 digit codes reliably: OK")


def test_recipient_isolation():
    print("[6] Testing Recipient Matching & Case/Dot Isolation...")
    client = mail_engine.IMAPMailClient("dummy@gmail.com", "secret")

    import email.message
    # Case 1: Case-permutation disambiguation
    # Account 1 target: AARABHArYAn53@gmail.com
    # Account 2 target: AARABHArYaN53@gmail.com
    msg1 = email.message.Message()
    msg1["To"] = "AARABHArYAn53@gmail.com"
    msg1["Delivered-To"] = "aarabharyan53@gmail.com"

    msg2 = email.message.Message()
    msg2["To"] = "AARABHArYaN53@gmail.com"
    msg2["Delivered-To"] = "aarabharyan53@gmail.com"

    # msg1 should match Account 1, but NOT Account 2
    matched1, r1 = client._match_recipient(msg1, "AARABHArYAn53@gmail.com")
    assert matched1 is True, f"Expected match, got {r1}"

    matched2, r2 = client._match_recipient(msg1, "AARABHArYaN53@gmail.com")
    assert matched2 is False, f"Account 2 must NOT match Account 1 email! Got: {matched2}, {r2}"

    # msg2 should match Account 2, but NOT Account 1
    matched3, r3 = client._match_recipient(msg2, "AARABHArYaN53@gmail.com")
    assert matched3 is True, f"Expected match, got {r3}"

    matched4, r4 = client._match_recipient(msg2, "AARABHArYAn53@gmail.com")
    assert matched4 is False, f"Account 1 must NOT match Account 2 email! Got: {matched4}, {r4}"

    # Case 2: Dot-permutation disambiguation
    msg_dot1 = email.message.Message()
    msg_dot1["To"] = "john.doe@gmail.com"
    msg_dot1["Delivered-To"] = "johndoe@gmail.com"

    matched_dot1, _ = client._match_recipient(msg_dot1, "john.doe@gmail.com")
    assert matched_dot1 is True

    matched_dot2, _ = client._match_recipient(msg_dot1, "j.ohndoe@gmail.com")
    assert matched_dot2 is False, "Different dot permutation must not match!"

    # Case 3: Delivered-To fallback ONLY when To is absent
    msg_noto = email.message.Message()
    msg_noto["Delivered-To"] = "johndoe@gmail.com"
    matched_noto, _ = client._match_recipient(msg_noto, "john.doe@gmail.com")
    assert matched_noto is True

    print(" -> Case and dot permutation isolation prevents cross-account OTP stealing: OK")


def main_test():
    print("==================================================================")
    print("RUNNING COMPREHENSIVE MAIL MULTIPLEXER & BINDING SYSTEM TESTS")
    print("==================================================================")
    test_permutation_generator()
    test_provider_detection()
    test_mail_vault_isolation()
    test_keyboards_and_ui()
    test_code_regex_and_extractors()
    test_recipient_isolation()
    print("==================================================================")
    print("ALL TESTS PASSED SUCCESSFULLY! (100% VERIFIED)")
    print("==================================================================")


if __name__ == "__main__":
    main_test()
