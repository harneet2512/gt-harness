import { useEffect,useRef } from "react";
import { onPalette } from "../palette";

export default function SurveyorPortrait({color}:{color:string}) {
  const host=useRef<HTMLSpanElement>(null);
  useEffect(()=>{
    let cancelled=false,dispose:(()=>void)|undefined;
    const draw=async()=>{
      const {drawSurveyorPortrait}=await import("../three/surveyorPortrait");
      if(cancelled||!host.current)return;
      dispose?.();
      const canvas=document.createElement("canvas");canvas.style.width="100%";canvas.style.height="100%";
      host.current.replaceChildren(canvas);
      dispose=drawSurveyorPortrait(canvas,color,document.documentElement.dataset.theme==="dark");
    };
    void draw();const unsubscribe=onPalette(()=>void draw());
    return ()=>{cancelled=true;unsubscribe();dispose?.();};
  },[color]);
  return <span className="surveyor-portrait" ref={host} role="img" aria-label="GT Surveyor agent vehicle"/>;
}
