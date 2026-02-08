#!/usr/bin/env python3
"""Test YAML configuration file loading for nrod_railhub."""

import argparse
import pathlib
import pytest
import tempfile
import yaml

from nrod_railhub.cli import load_config_file, merge_config_with_args


def test_load_config_file_valid():
    """Test loading a valid YAML configuration file."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        yaml.dump({
            'user': 'test@example.com',
            'password': 'testpass',
            'headcode': '2C90',
            'width': 120,
        }, f)
        temp_path = f.name
    
    try:
        config = load_config_file(temp_path)
        assert config['user'] == 'test@example.com'
        assert config['password'] == 'testpass'
        assert config['headcode'] == '2C90'
        assert config['width'] == 120
    finally:
        pathlib.Path(temp_path).unlink()


def test_load_config_file_empty():
    """Test loading an empty YAML file returns empty dict."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        f.write('')
        temp_path = f.name
    
    try:
        config = load_config_file(temp_path)
        assert config == {}
    finally:
        pathlib.Path(temp_path).unlink()


def test_load_config_file_not_found():
    """Test that FileNotFoundError is raised for missing file."""
    with pytest.raises(FileNotFoundError):
        load_config_file('/nonexistent/config.yaml')


def test_load_config_file_invalid_yaml():
    """Test that YAMLError is raised for invalid YAML."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        f.write('invalid: yaml: content: [')
        temp_path = f.name
    
    try:
        with pytest.raises(yaml.YAMLError):
            load_config_file(temp_path)
    finally:
        pathlib.Path(temp_path).unlink()


def test_load_config_file_not_dict():
    """Test that ValueError is raised when config is not a dict."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        f.write('- item1\n- item2\n')  # This is a list, not a dict
        temp_path = f.name
    
    try:
        with pytest.raises(ValueError, match="must contain a YAML dictionary"):
            load_config_file(temp_path)
    finally:
        pathlib.Path(temp_path).unlink()


def test_load_config_file_expanduser():
    """Test that tilde paths are expanded."""
    # Create a config in a temp directory (can't easily test ~ expansion without touching home)
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = pathlib.Path(tmpdir) / 'config.yaml'
        with open(config_path, 'w') as f:
            yaml.dump({'user': 'test@example.com'}, f)
        
        config = load_config_file(str(config_path))
        assert config['user'] == 'test@example.com'


def test_merge_config_with_args_basic():
    """Test merging config values into args namespace."""
    args = argparse.Namespace(
        user=None,
        password=None,
        headcode=None,
        width=96,
        td_area=[],
        verbose=False,
    )
    
    defaults = {
        'user': None,
        'password': None,
        'headcode': None,
        'width': 96,
        'td_area': [],
        'verbose': False,
    }
    
    config = {
        'user': 'config@example.com',
        'password': 'configpass',
        'headcode': '2C90',
    }
    
    merged = merge_config_with_args(args, config, defaults)
    assert merged.user == 'config@example.com'
    assert merged.password == 'configpass'
    assert merged.headcode == '2C90'
    assert merged.width == 96  # Not in config, keeps original


def test_merge_config_with_args_cli_overrides():
    """Test that command-line args override config file values."""
    args = argparse.Namespace(
        user='cli@example.com',  # Set via CLI
        password=None,           # Not set via CLI
        headcode='1A23',         # Set via CLI
        width=96,
        td_area=[],
    )
    
    defaults = {
        'user': None,
        'password': None,
        'headcode': None,
        'width': 96,
        'td_area': [],
    }
    
    config = {
        'user': 'config@example.com',
        'password': 'configpass',
        'headcode': '2C90',
    }
    
    merged = merge_config_with_args(args, config, defaults)
    # CLI value should be preserved (not at default)
    assert merged.user == 'cli@example.com'
    # Config value should be used when CLI is None (at default)
    assert merged.password == 'configpass'
    # CLI value should be preserved (not at default)
    assert merged.headcode == '1A23'


def test_merge_config_with_args_list_handling():
    """Test that list values (like td_area) are handled correctly."""
    args = argparse.Namespace(
        td_area=[],  # Empty list
        user=None,
    )
    
    defaults = {
        'td_area': [],
        'user': None,
    }
    
    config = {
        'td_area': ['EK', 'WR'],
        'user': 'test@example.com',
    }
    
    merged = merge_config_with_args(args, config, defaults)
    assert merged.td_area == ['EK', 'WR']
    assert merged.user == 'test@example.com'


def test_merge_config_with_args_list_not_overridden():
    """Test that non-empty lists from CLI are not overridden."""
    args = argparse.Namespace(
        td_area=['XX'],  # Has CLI value
    )
    
    defaults = {
        'td_area': [],
    }
    
    config = {
        'td_area': ['EK', 'WR'],
    }
    
    merged = merge_config_with_args(args, config, defaults)
    # Should keep CLI value
    assert merged.td_area == ['XX']


def test_merge_config_with_args_hyphen_to_underscore():
    """Test that hyphenated keys in YAML are converted to underscored attributes."""
    args = argparse.Namespace(
        corpus_cache='~/.cache/openraildata/CORPUSExtract.json',
        smart_refresh=False,
        log_level='error',
    )
    
    defaults = {
        'corpus_cache': '~/.cache/openraildata/CORPUSExtract.json',
        'smart_refresh': False,
        'log_level': 'error',
    }
    
    config = {
        'corpus-cache': '~/custom/corpus.json',  # Hyphenated in YAML
        'smart-refresh': True,                   # Hyphenated in YAML
        'log_level': 'info',                     # Underscored in YAML
    }
    
    merged = merge_config_with_args(args, config, defaults)
    assert merged.corpus_cache == '~/custom/corpus.json'
    assert merged.smart_refresh is True
    assert merged.log_level == 'info'


def test_merge_config_with_args_unknown_keys_ignored():
    """Test that unknown config keys are ignored."""
    args = argparse.Namespace(
        user=None,
    )
    
    defaults = {
        'user': None,
    }
    
    config = {
        'user': 'test@example.com',
        'unknown_option': 'should_be_ignored',
        'another_unknown': 123,
    }
    
    merged = merge_config_with_args(args, config, defaults)
    assert merged.user == 'test@example.com'
    assert not hasattr(merged, 'unknown_option')
    assert not hasattr(merged, 'another_unknown')


def test_merge_config_with_args_boolean_flags():
    """Test that boolean flags are handled correctly."""
    args = argparse.Namespace(
        verbose=False,
        corpus_refresh=False,
        interactive=False,
    )
    
    defaults = {
        'verbose': False,
        'corpus_refresh': False,
        'interactive': False,
    }
    
    config = {
        'verbose': True,
        'corpus_refresh': True,
    }
    
    merged = merge_config_with_args(args, config, defaults)
    assert merged.verbose is True
    assert merged.corpus_refresh is True
    assert merged.interactive is False  # Not in config, stays False


def test_merge_config_with_args_null_values():
    """Test that null/None values in config don't override defaults."""
    args = argparse.Namespace(
        headcode='2C90',
        width=96,
        user=None,
    )
    
    defaults = {
        'headcode': None,
        'width': 96,
        'user': None,
    }
    
    config = {
        'headcode': None,  # Explicit null in YAML
        'width': 120,
        'user': 'test@example.com',
    }
    
    merged = merge_config_with_args(args, config, defaults)
    # None in config should not override existing value
    assert merged.headcode == '2C90'
    # Actual value in config should be used
    assert merged.width == 120
    # None arg with value in config should be set
    assert merged.user == 'test@example.com'


def test_merge_config_with_args_integer_values():
    """Test that integer values are correctly merged."""
    args = argparse.Namespace(
        port=61618,
        width=96,
        status_every=15,
        web_port=8088,
    )
    
    defaults = {
        'port': 61618,
        'width': 96,
        'status_every': 15,
        'web_port': 8088,
    }
    
    config = {
        'width': 120,
        'status_every': 30,
    }
    
    merged = merge_config_with_args(args, config, defaults)
    assert merged.port == 61618  # Not in config
    assert merged.width == 120   # From config
    assert merged.status_every == 30  # From config
    assert merged.web_port == 8088  # Not in config
