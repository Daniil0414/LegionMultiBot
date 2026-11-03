"""Create a source distribution from an explicit allowlist, without user state."""
import argparse
import json
from pathlib import Path
import zipfile


SOURCE_FILES = (
    'app_version.py', 'app_updater.py', 'app_update_ui.py',
    'app_paths.py', 'portable_entry.py', 'portable_self_test.py',
    'stealth_shield/manifest.json', 'stealth_shield/shield_engine.js',
    'app_container.py', 'browser_profiles.py', 'browser_focus.py', 'chrome_mobile.py', 'browser_lifecycle.py', 'chrome_transport.py',
    'gui_launcher.py', 'happ_memory.py', 'proxy_manager.py', 'proxy_network.py',
    'vpn_dispatcher.py', 'vpn_runtime.py', 'vpn_failover.py', 'secure_launcher.py',
    'userscripts.py', 'userscript_runtime.js',
    'userscript_privacy.py', 'userscript_privacy.js', 'userscript_transform.js', 'mine_control_ui.py', 'account_loading_ui.py', 'embedded_login_ui.py',
    'vendor/acorn.js', 'vendor/acorn.LICENSE',
    'tests/test_userscripts.py', 'tests/userscript_runtime.test.js', 'tests/test_app_updater.py',
    'tests/test_userscript_privacy.py', 'tests/userscript_transform.test.js', 'tests/test_mine_control.py', 'tests/test_account_loading.py', 'tests/test_embedded_login.py',
    'tests/test_native_browser.py', 'tests/test_docked_browser.py', 'tests/test_gui_layout.py', 'tests/test_browser_focus.py', 'tests/test_vpn_lifecycle.py',
    'server.js', 'secure_launch.js', 'manager_access.js', 'quick_launch.js',
    'public/index.html', 'public/app.js', 'public/style.css', 'README.md', 'package_app.py',
    'tests/test_vpn_safety.py', 'tests/test_vpn_failover.py', 'tests/test_network_mode.py', 'tests/test_subscription.py', 'tests/test_mobile_browser.py', 'tests/test_package_app.py', 'tests/test_browser_lifecycle.py', 'tests/test_chrome_transport.py',
    'tests/secure_launch.test.js', 'tests/manager_access.test.js', 'tests/server_access.test.js',
    'tests/test_shutdown.py', 'tests/manager_lifecycle.test.js',
    'START_MULTIBOT.bat', 'START_WEB_MANAGER.bat', 'Запустить_Мультибот.bat',
    'Запустить_Менеджер_Профилей.bat', 'Запустить_Менеджер_Ботов_GUI.bat',
    'Запустить_Всех_Ботов_Быстро.bat',
)


def build_package(root, output):
    root, output = Path(root).resolve(), Path(output).resolve()
    development_readme = root / 'docs' / 'DEVELOPMENT.md'
    if development_readme.is_file() and not development_readme.resolve().is_relative_to(root):
        raise ValueError('External development documentation')
    sources = [root / name for name in SOURCE_FILES]
    for source in sources:
        if not source.is_file() or not source.resolve().is_relative_to(root):
            raise ValueError('Missing or external source file: ' + source.name)
    if output.suffix.lower() != '.zip' or output in sources:
        raise ValueError('Choose a separate .zip output path')
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name in SOURCE_FILES:
            source = development_readme if name == 'README.md' and development_readme.is_file() else root / name
            archive.write(source, name)
        archive.writestr('vpn_config.json', json.dumps(dict(subscription_url='', raw_nodes_text='',
                                                          last_updated='', active_mode='auto')))
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='dist/legion-multibot.zip')
    options = parser.parse_args()
    print(build_package(Path(__file__).parent, options.output))
