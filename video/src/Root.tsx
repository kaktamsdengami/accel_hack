import { Composition } from "remotion";
import { Demo, FPS, TOTAL_S } from "./Demo";

export const Root = () => (
  <Composition id="Demo" component={Demo} durationInFrames={TOTAL_S * FPS} fps={FPS} width={1920} height={1080} />
);
