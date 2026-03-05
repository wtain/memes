--
-- PostgreSQL database dump
--

-- Dumped from database version 16.11 (Debian 16.11-1.pgdg13+1)
-- Dumped by pg_dump version 18.2

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Extensions
--
CREATE EXTENSION IF NOT EXISTS vector;


--
-- Name: embeddings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.embeddings (
    id uuid NOT NULL,
    image_id uuid,
    text text NOT NULL,
    confidence double precision,
    created_at timestamp without time zone DEFAULT now()
);


--
-- Name: image_metrics; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.image_metrics (
    image_id uuid NOT NULL,
    read_time_ms numeric,
    preprocess_time_ms numeric,
    ocr_time_ms numeric,
    total_time_ms numeric,
    created_at timestamp without time zone DEFAULT now()
);


--
-- Name: image_processing_status; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.image_processing_status (
    image_id uuid NOT NULL,
    pipeline character varying NOT NULL,
    status character varying NOT NULL,
    started_at timestamp without time zone,
    finished_at timestamp without time zone,
    error_message text
);


--
-- Name: images; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.images (
    id uuid NOT NULL,
    filename character varying NOT NULL,
    content_hash character varying,
    width integer,
    height integer,
    created_at timestamp without time zone DEFAULT now()
);


--
-- Name: ocr_texts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ocr_texts (
    id uuid NOT NULL,
    image_id uuid,
    text text NOT NULL,
    confidence double precision,
    bbox json,
    language character varying(8),
    created_at timestamp without time zone DEFAULT now()
);


--
-- Name: processing_errors; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.processing_errors (
    id uuid NOT NULL,
    image_id uuid,
    stage character varying NOT NULL,
    message text NOT NULL,
    created_at timestamp without time zone DEFAULT now()
);


--
-- Name: embeddings embeddings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.embeddings
    ADD CONSTRAINT embeddings_pkey PRIMARY KEY (id);


--
-- Name: image_metrics image_metrics_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.image_metrics
    ADD CONSTRAINT image_metrics_pkey PRIMARY KEY (image_id);


--
-- Name: image_processing_status image_processing_status_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.image_processing_status
    ADD CONSTRAINT image_processing_status_pkey PRIMARY KEY (image_id, pipeline);


--
-- Name: images images_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.images
    ADD CONSTRAINT images_pkey PRIMARY KEY (id);


--
-- Name: ocr_texts ocr_texts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ocr_texts
    ADD CONSTRAINT ocr_texts_pkey PRIMARY KEY (id);


--
-- Name: processing_errors processing_errors_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.processing_errors
    ADD CONSTRAINT processing_errors_pkey PRIMARY KEY (id);


--
-- Name: ix_images_content_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_images_content_hash ON public.images USING btree (content_hash);


--
-- Name: ix_images_filename; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_images_filename ON public.images USING btree (filename);


--
-- Name: embeddings embeddings_image_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.embeddings
    ADD CONSTRAINT embeddings_image_id_fkey FOREIGN KEY (image_id) REFERENCES public.images(id) ON DELETE CASCADE;


--
-- Name: image_metrics image_metrics_image_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.image_metrics
    ADD CONSTRAINT image_metrics_image_id_fkey FOREIGN KEY (image_id) REFERENCES public.images(id) ON DELETE CASCADE;


--
-- Name: image_processing_status image_processing_status_image_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.image_processing_status
    ADD CONSTRAINT image_processing_status_image_id_fkey FOREIGN KEY (image_id) REFERENCES public.images(id) ON DELETE CASCADE;


--
-- Name: ocr_texts ocr_texts_image_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ocr_texts
    ADD CONSTRAINT ocr_texts_image_id_fkey FOREIGN KEY (image_id) REFERENCES public.images(id) ON DELETE CASCADE;


--
-- Name: processing_errors processing_errors_image_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.processing_errors
    ADD CONSTRAINT processing_errors_image_id_fkey FOREIGN KEY (image_id) REFERENCES public.images(id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--

