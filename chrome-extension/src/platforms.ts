import type { PlatformConfig, PlatformKey } from './types'

export const DEFAULT_SERVER_URL = ''

export const PLATFORMS: Record<PlatformKey, PlatformConfig> = {
  taobao: {
    name: '淘宝/天猫',
    domains: ['.taobao.com', '.tmall.com'],
    sourceId: 'platform-taobao',
  },
  sycm: {
    name: '生意参谋',
    domains: ['.sycm.taobao.com', '.taobao.com'],
    sourceId: 'platform-sycm',
  },
  alimama: {
    name: '阿里妈妈',
    domains: ['.alimama.com', '.taobao.com'],
    sourceId: 'platform-alimama',
  },
  douyin: {
    name: '抖店',
    domains: ['.douyin.com', '.jinritemai.example.test'],
    sourceId: 'platform-douyin',
  },
  jd: {
    name: '京东',
    domains: ['.jd.com'],
    sourceId: 'platform-jd',
  },
  pdd: {
    name: '拼多多',
    domains: ['.pinduoduo.com', '.yangkeduo.com'],
    sourceId: 'platform-pdd',
  },
}

export const PLATFORM_KEYS = Object.keys(PLATFORMS) as PlatformKey[]
