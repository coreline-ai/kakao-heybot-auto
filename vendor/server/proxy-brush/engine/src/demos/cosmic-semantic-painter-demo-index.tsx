import React from "react";
import {Composition, registerRoot} from "remotion";
import {CosmicSemanticPainterDemo} from "./CosmicSemanticPainterDemo";

const Root: React.FC = () => <Composition
  id="CosmicSemanticPainterDemo"
  component={CosmicSemanticPainterDemo}
  width={1920}
  height={1080}
  fps={30}
  durationInFrames={300}
/>;

registerRoot(Root);
