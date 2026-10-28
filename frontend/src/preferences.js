import { useEffect, useState } from 'react';

// Keep display preferences in this browser tab; never cache market data.
export function usePreference(key, initial) {
  const storageKey = 'dashboard.ui.v1.' + key;
  const read = () => {
    try { const value = sessionStorage.getItem(storageKey); if (value !== null) return JSON.parse(value); } catch {}
    return typeof initial === 'function' ? initial() : initial;
  };
  const [saved, setSaved] = useState(() => ({key, value: read()}));
  const current = saved.key === key ? saved : {key, value: read()};
  if (saved.key !== key) setSaved(current);
  useEffect(() => {
    try { sessionStorage.setItem(storageKey, JSON.stringify(current.value)); } catch {}
  }, [storageKey, current.value]);
  return [current.value, value => setSaved(previous => ({key, value: typeof value === 'function' ? value(previous.value) : value}))];
}


export function selectedProduct(fallback = '') {
  try { return sessionStorage.getItem('dashboard.ui.v1.product') || fallback; } catch { return fallback; }
}
export function rememberProduct(value) {
  const product = value?.split('.')[0].toUpperCase();
  if (!product || product === 'ALL' || !/^[A-Z]{1,3}$/.test(product)) return;
  try { sessionStorage.setItem('dashboard.ui.v1.product', product); } catch {}
}
