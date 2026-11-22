import { describe, expect, it } from 'vitest'
import { DEFAULT_BACKEND_ORIGIN, backendHostPermission, backendOrigin } from './backend-origin.js'

describe('backendHostPermission', () => {
  it.each([undefined, null, '', '   '])('falls back to the default for %j', (v) => {
    expect(backendHostPermission(v)).toBe(`${DEFAULT_BACKEND_ORIGIN}/*`)
    expect(DEFAULT_BACKEND_ORIGIN).toBe('http://127.0.0.1:8000')
  })
  it.each([
    ['http://127.0.0.1:8000', 'http://127.0.0.1:8000/*'],
    ['https://assist.example.com', 'https://assist.example.com/*'],
    ['https://assist.example.com/', 'https://assist.example.com/*'],
    [' https://Assist.Example.com:8443 ', 'https://assist.example.com:8443/*'],
    ['http://localhost:9000', 'http://localhost:9000/*'],
  ])('accepts %j', (input, expected) => {
    expect(backendHostPermission(input)).toBe(expected)
  })
  it.each([
    'javascript:alert(1)',
    'file:///etc/passwd',
    'ftp://example.com',
    'chrome-extension://abc',
    'https://*.example.com',
    'https://*',
    '*://example.com',
    '<all_urls>',
    'example.com',
    'http://',
    'https://user:pw@example.com',
    'https://example.com/api',
    'https://example.com/?x=1',
    'https://example.com/#frag',
    'not a url',
  ])('rejects %j', (input) => {
    expect(() => backendHostPermission(input)).toThrow()
  })
})

describe('backendOrigin', () => {
  it('returns the validated origin without a path or wildcard', () => {
    expect(backendOrigin(undefined)).toBe(DEFAULT_BACKEND_ORIGIN)
    expect(backendOrigin(' https://Assist.Example.com:8443/ ')).toBe('https://assist.example.com:8443')
  })
  it('rejects what the host permission rejects', () => {
    expect(() => backendOrigin('javascript:alert(1)')).toThrow()
    expect(() => backendOrigin('https://*.example.com')).toThrow()
    expect(() => backendOrigin('https://example.com/api')).toThrow()
  })
})
