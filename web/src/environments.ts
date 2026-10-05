import type { EnvironmentValue } from "./api";

export interface EnvironmentOption {
  value: EnvironmentValue;
  label: string;
  isReal: boolean;
}

export const ENVIRONMENTS: EnvironmentOption[] = [
  { value: "domestic_real", label: "국내 실전", isReal: true },
  { value: "us_real", label: "미국 실전", isReal: true },
  { value: "domestic_paper", label: "국내 모의", isReal: false },
  { value: "us_paper", label: "미국 모의", isReal: false },
];

export const DEFAULT_ENVIRONMENT: EnvironmentValue = "domestic_paper";
const STORAGE_KEY = "stock-bot:environment";

/** 저장값이 없거나 잘못되었거나 저장소를 쓸 수 없으면 국내 모의로 연다. 실전으로 넘어가지 않는다. */
export function loadEnvironment(): EnvironmentValue {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    const match = ENVIRONMENTS.find((env) => env.value === saved);
    return match ? match.value : DEFAULT_ENVIRONMENT;
  } catch {
    return DEFAULT_ENVIRONMENT;
  }
}

export function saveEnvironment(value: EnvironmentValue): void {
  try {
    localStorage.setItem(STORAGE_KEY, value);
  } catch {
    // 저장에 실패해도 현재 화면은 그대로 동작한다.
  }
}

export function findEnvironment(value: EnvironmentValue): EnvironmentOption {
  return ENVIRONMENTS.find((env) => env.value === value) ?? ENVIRONMENTS[2];
}
