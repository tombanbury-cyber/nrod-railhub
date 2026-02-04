---
description: "Instructions for test files"
applyTo: "tests/**/*.py"
---

# Testing Guidelines

## Test Framework

- Use **pytest** for all tests
- Test files must start with `test_` prefix
- Test functions must start with `test_` prefix

## Test Structure

```python
import pytest
from nrod_railhub.resolvers import LocationResolver

def test_feature_name():
    """Test description explaining what is being tested."""
    # Arrange
    resolver = LocationResolver()
    
    # Act
    result = resolver.name_for_tiploc("CLPHMJC")
    
    # Assert
    assert result is not None
    assert "Clapham Junction" in result
```

## What to Test

### Unit Tests
- Reference data parsers (CORPUS, SMART, SCHEDULE)
- Location resolution logic
- Message parsing (VSTP, TRUST, TD)
- Database operations (without requiring live STOMP)

### Integration Tests
- End-to-end message flow (use mock data)
- Database schema integrity
- Web dashboard routes

### Not Required
- Live STOMP connections (manual testing only)
- Network Rail API calls (mock the responses)

## Test Data

- Store test fixtures in test files as constants
- Use real message examples (anonymized if needed)
- Include edge cases (missing fields, double-encoding, etc.)

## Running Tests

```bash
# All tests
pytest -q

# Specific file
pytest tests/test_double_encoding.py -v

# With coverage
pytest --cov=nrod_railhub tests/

# Verbose output
pytest -vv
```

## Best Practices

- Keep tests fast (< 1s each)
- Tests should be independent (no shared state)
- Mock external dependencies (HTTP calls, file I/O)
- Use descriptive test names
- One assertion per logical concept
- Test both success and failure cases

## Common Patterns

### Testing Reference Data Parsing

```python
def test_corpus_parsing():
    """Test CORPUS JSON parsing handles both formats."""
    # Test array format
    data1 = [{"TIPLOC": "TEST", "NLCDESC": "Test Station"}]
    # Test object format  
    data2 = {"TIPLOCDATA": [{"TIPLOC": "TEST", "NLCDESC": "Test Station"}]}
```

### Testing Double-Encoding

```python
def test_double_encoded_json():
    """Test handling of double-encoded JSON from Network Rail."""
    # Network Rail sometimes double-encodes responses
    json_str = '\"[{\\"field\\":\\"value\\"}]\"'
    result = decode_smart_data(json_str)
    assert isinstance(result, list)
```

### Mocking STOMP Messages

```python
def test_message_handling():
    """Test STOMP message processing."""
    mock_frame = Mock()
    mock_frame.body = '{"msg_type": "CA", "data": {...}}'
    listener.on_message(mock_frame)
    # Assert expected state changes
```

## Continuous Integration

- Tests run automatically on all PRs via GitHub Actions
- All tests must pass before merge
- Monthly scheduled tests with fresh reference data download
