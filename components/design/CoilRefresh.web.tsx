import React, { useEffect, useRef } from 'react';
import { Animated, ScrollView, View, type NativeScrollEvent, type NativeSyntheticEvent } from 'react-native';
import type { CoilRefreshProps } from '@/components/design/CoilRefresh';
import { decide, PullCoil, usePull } from '@/components/design/coilPull';

/**
 * Pull to refresh with the Zitch coil, web (17 Nov 2026: "dragging down … should
 * reload the page, it should not affect the scrolling").
 *
 * The browser scrolls the list itself, so the pull listens beside it with plain
 * touch and mouse events on the whole screen, the still part at the top
 * included. A drag only becomes a pull once it has gone SLOP px down, and on the
 * scrolling part only while the list is at its top; until then (and for any
 * drag that goes up first) nothing is prevented, so the browser's scrolling,
 * taps and text fields behave exactly as before. A mouse drag that became a pull
 * does not also click whatever it ended on.
 */
export const CoilRefreshScrollView = ({ refreshing, onRefresh, children, onScroll, top, ...scroll }: CoilRefreshProps) => {
  const p = usePull(refreshing, onRefresh);
  const pRef = useRef(p);
  pRef.current = p;
  const root = useRef<View>(null);
  const topRef = useRef<View>(null);
  const scrollRef = useRef<ScrollView>(null);

  useEffect(() => {
    const el = root.current as unknown as HTMLElement | null;
    if (!el || typeof el.addEventListener !== 'function') return;
    let startY = 0;
    let state: 'idle' | 'wait' | 'pull' = 'idle';
    let onTop = false;
    const scrollTop = () => {
      const node = (scrollRef.current as unknown as { getScrollableNode?: () => HTMLElement })?.getScrollableNode?.();
      return node ? node.scrollTop : 0;
    };
    const begin = (y: number, target: EventTarget | null) => {
      const topEl = topRef.current as unknown as HTMLElement | null;
      onTop = !!(topEl && target instanceof Node && topEl.contains(target));
      startY = y;
      state = 'wait';
    };
    // true while this drag is a pull (the caller then keeps the browser out of it)
    const moveTo = (y: number) => {
      if (state === 'idle') return false;
      const dy = y - startY;
      if (state === 'wait') {
        const d = decide(dy, { onTop, scrollY: scrollTop(), busy: pRef.current.busy() });
        if (d === 'scroll') { state = 'idle'; return false; }
        if (d === 'wait') return false;
        state = 'pull';
        pRef.current.begin();
        el.style.userSelect = 'none';
      }
      pRef.current.move(dy);
      return true;
    };
    const finish = (cancelled: boolean) => {
      const wasPull = state === 'pull';
      state = 'idle';
      el.style.userSelect = '';
      if (!wasPull) return false;
      if (cancelled) pRef.current.cancel();
      else pRef.current.end();
      return true;
    };

    const onTouchStart = (e: TouchEvent) => { if (e.touches.length === 1) begin(e.touches[0].clientY, e.target); else state = 'idle'; };
    const onTouchMove = (e: TouchEvent) => { if (moveTo(e.touches[0]?.clientY ?? startY) && e.cancelable) e.preventDefault(); };
    const onTouchEnd = () => { finish(false); };
    const onTouchCancel = () => { finish(true); };

    const onMouseMove = (e: MouseEvent) => { if (moveTo(e.clientY)) e.preventDefault(); };
    const swallowClick = (e: MouseEvent) => { e.stopPropagation(); e.preventDefault(); };
    const onMouseUp = () => {
      window.removeEventListener('mousemove', onMouseMove);
      window.removeEventListener('mouseup', onMouseUp);
      if (finish(false)) {
        // the drag ended a pull, not a tap: the click that follows goes nowhere
        window.addEventListener('click', swallowClick, { capture: true, once: true });
        setTimeout(() => window.removeEventListener('click', swallowClick, { capture: true }), 0);
      }
    };
    const onMouseDown = (e: MouseEvent) => {
      if (e.button !== 0) return;
      const t = e.target as HTMLElement | null;
      if (t && t.closest && t.closest('input,textarea,select,[contenteditable="true"]')) return;
      begin(e.clientY, e.target);
      window.addEventListener('mousemove', onMouseMove);
      window.addEventListener('mouseup', onMouseUp);
    };

    el.addEventListener('touchstart', onTouchStart, { passive: true });
    el.addEventListener('touchmove', onTouchMove, { passive: false });
    el.addEventListener('touchend', onTouchEnd);
    el.addEventListener('touchcancel', onTouchCancel);
    el.addEventListener('mousedown', onMouseDown);
    return () => {
      el.removeEventListener('touchstart', onTouchStart);
      el.removeEventListener('touchmove', onTouchMove);
      el.removeEventListener('touchend', onTouchEnd);
      el.removeEventListener('touchcancel', onTouchCancel);
      el.removeEventListener('mousedown', onMouseDown);
      window.removeEventListener('mousemove', onMouseMove);
      window.removeEventListener('mouseup', onMouseUp);
    };
  }, []);

  return (
    <View ref={root} style={{ flex: 1 }}>
      {top ? <View ref={topRef}>{top}</View> : null}
      <View style={{ flex: 1 }}>
        {p.coil ? <PullCoil pull={p.pull} /> : null}
        <Animated.View style={{ flex: 1, transform: [{ translateY: p.pull }] }}>
          <ScrollView ref={scrollRef} {...scroll}
            onScroll={(e: NativeSyntheticEvent<NativeScrollEvent>) => onScroll?.(e)} scrollEventThrottle={16}
            // the list's own overscroll stays inside it: no browser bounce or page reload on top of the coil
            style={[{ flex: 1 }, ({ overscrollBehaviorY: 'contain' } as object), scroll.style]}>
            {children}
          </ScrollView>
        </Animated.View>
      </View>
    </View>
  );
};

export default CoilRefreshScrollView;
