package com.minierp.plm;

import org.springframework.data.jpa.repository.JpaRepository;
import java.util.List;
import java.util.Optional;

public interface PlmDocumentRepository extends JpaRepository<PlmDocument, Long> {
    Optional<PlmDocument> findByDocNoAndStatus(String docNo, String status);
}
