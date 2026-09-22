'use client';

import { useState } from 'react';
import Image from 'next/image';
import { ChevronLeft, ChevronRight } from 'lucide-react';

interface Props {
  images: string[];
  title: string;
}

/** Carousel foto TikTok: gantian pemutar video, navigasi ← →. */
export default function PhotoCarousel({ images, title }: Props) {
  const [index, setIndex] = useState(0);
  if (!images.length) return null;

  const prev = () => setIndex((i) => (i - 1 + images.length) % images.length);
  const next = () => setIndex((i) => (i + 1) % images.length);

  return (
    <div className="photo-carousel" role="region" aria-label={`Foto: ${title}`}>
      <Image
        src={images[index]}
        alt={`${title} — foto ${index + 1} dari ${images.length}`}
        width={720}
        height={960}
        className="photo-carousel-img"
        sizes="(max-width: 900px) 100vw, 720px"
      />
      {images.length > 1 && (
        <>
          <button type="button" className="photo-carousel-btn prev" onClick={prev} aria-label="Foto sebelumnya">
            <ChevronLeft size={20} aria-hidden="true" />
          </button>
          <button type="button" className="photo-carousel-btn next" onClick={next} aria-label="Foto berikutnya">
            <ChevronRight size={20} aria-hidden="true" />
          </button>
          <span className="photo-carousel-counter" aria-live="polite">
            {index + 1}/{images.length}
          </span>
        </>
      )}
    </div>
  );
}
